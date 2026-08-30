"""Tests for AUTH_MODE — running with and without Authentik SSO (issue #13).

Each scenario runs in its own subprocess: AUTH_MODE is read once when `app` is
imported, so two modes cannot coexist in a single interpreter. Isolation also
keeps these tests from dictating import order for anything else that imports
the Flask app.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(script: str, **env_overrides) -> subprocess.CompletedProcess:
    """Run `script` against a fresh app import and isolated DATA_DIR."""
    env = os.environ.copy()
    env['DATA_DIR'] = tempfile.mkdtemp()
    # Never inherit the developer's real SSO config.
    for key in ('AUTH_MODE', 'AUTH_LOCAL_USERNAME',
                'AUTHENTIK_CLIENT_ID', 'AUTHENTIK_CLIENT_SECRET'):
        env.pop(key, None)
    env.update(env_overrides)

    preamble = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {ROOT!r})
        sys.path.insert(0, {os.path.join(ROOT, 'ui')!r})

        def emit(**kw):
            print('__RESULT__' + json.dumps(kw))
    """)
    return subprocess.run(
        [sys.executable, '-c', preamble + textwrap.dedent(script)],
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=180,
    )


def _result(proc: subprocess.CompletedProcess) -> dict:
    for line in proc.stdout.splitlines():
        if line.startswith('__RESULT__'):
            return json.loads(line[len('__RESULT__'):])
    raise AssertionError(
        f'no result emitted (exit={proc.returncode})\n'
        f'--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}'
    )


class TestAuthModeNone(unittest.TestCase):
    """AUTH_MODE=none must make the app usable with no identity provider."""

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            from app import app
            app.config['TESTING'] = True
            client = app.test_client()

            jobs = client.get('/api/jobs')
            me = client.get('/api/me')
            status = client.get('/api/auth/status')
            login = client.get('/login')
            logout = client.get('/logout')
            sso = client.get('/auth/login')

            emit(
                jobs_status=jobs.status_code,
                me_status=me.status_code,
                me=me.get_json(),
                auth_status=status.get_json(),
                login_status=login.status_code,
                login_location=login.headers.get('Location'),
                logout_status=logout.status_code,
                logout_location=logout.headers.get('Location'),
                sso_status=sso.status_code,
                sso_location=sso.headers.get('Location'),
            )
        """, AUTH_MODE='none')
        cls.res = _result(proc)

    def test_protected_api_reachable_without_login(self):
        self.assertEqual(self.res['jobs_status'], 200)

    def test_requests_run_as_local_admin(self):
        self.assertEqual(self.res['me_status'], 200)
        self.assertEqual(self.res['me']['user'], 'local')
        self.assertTrue(self.res['me']['is_admin'])
        self.assertTrue(self.res['me']['auth_disabled'])

    def test_auth_status_reports_disabled(self):
        self.assertFalse(self.res['auth_status']['sso_enabled'])
        self.assertTrue(self.res['auth_status']['auth_disabled'])

    def test_login_page_redirects_to_app(self):
        self.assertEqual(self.res['login_status'], 302)
        self.assertEqual(self.res['login_location'], '/')

    def test_logout_is_a_noop_redirect(self):
        self.assertEqual(self.res['logout_status'], 302)
        self.assertEqual(self.res['logout_location'], '/')

    def test_sso_entrypoint_redirects_instead_of_erroring(self):
        self.assertEqual(self.res['sso_status'], 302)
        self.assertEqual(self.res['sso_location'], '/')


class TestAuthModeNoneLocalUser(unittest.TestCase):
    def test_local_username_is_configurable_and_persisted(self):
        proc = _run("""
            from app import app
            from database import get_user
            app.config['TESTING'] = True
            row = get_user('chef')
            me = app.test_client().get('/api/me').get_json()
            emit(
                user_row_exists=row is not None,
                user_row_is_admin=bool(row and row['is_admin']),
                user_row_has_no_oidc_sub=bool(row and row['oidc_sub'] is None),
                me_user=me['user'],
            )
        """, AUTH_MODE='none', AUTH_LOCAL_USERNAME='chef')
        res = _result(proc)
        self.assertTrue(res['user_row_exists'])
        self.assertTrue(res['user_row_is_admin'])
        self.assertTrue(res['user_row_has_no_oidc_sub'])
        self.assertEqual(res['me_user'], 'chef')


class TestDefaultModeStillProtected(unittest.TestCase):
    """Regression guard: the default must never fall open (issue #13 fix)."""

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            from app import app
            app.config['TESTING'] = True
            client = app.test_client()

            jobs = client.get('/api/jobs')
            login = client.get('/login')
            status = client.get('/api/auth/status')

            authed = app.test_client()
            with authed.session_transaction() as sess:
                sess['user'] = 'someone'
                sess['is_admin'] = False

            emit(
                jobs_status=jobs.status_code,
                jobs_body=jobs.get_json(),
                login_status=login.status_code,
                auth_status=status.get_json(),
                authed_jobs_status=authed.get('/api/jobs').status_code,
            )
        """)
        cls.res = _result(proc)

    def test_protected_api_still_401_without_session(self):
        self.assertEqual(self.res['jobs_status'], 401)
        self.assertEqual(self.res['jobs_body']['error'], 'Authentication required')

    def test_cookie_session_still_authenticates(self):
        self.assertEqual(self.res['authed_jobs_status'], 200)

    def test_login_page_served_so_the_error_is_visible(self):
        self.assertEqual(self.res['login_status'], 200)

    def test_auth_status_reports_enabled_mode(self):
        self.assertFalse(self.res['auth_status']['sso_enabled'])
        self.assertFalse(self.res['auth_status']['auth_disabled'])


class TestInvalidAuthMode(unittest.TestCase):
    def test_typo_fails_fast_instead_of_falling_open(self):
        proc = _run("""
            try:
                import app
            except RuntimeError as exc:
                emit(error=str(exc))
            else:
                emit(error=None)
        """, AUTH_MODE='disabled')
        res = _result(proc)
        self.assertIsNotNone(
            res['error'], 'an unrecognised AUTH_MODE must not boot the app'
        )
        self.assertIn('AUTH_MODE', res['error'])


if __name__ == '__main__':
    unittest.main()
