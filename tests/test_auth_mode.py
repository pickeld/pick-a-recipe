"""Tests for AUTH_MODE — local password accounts versus Authentik SSO.

Each scenario runs in its own subprocess: AUTH_MODE is read once when `app` is
imported, so two modes cannot coexist in a single interpreter. Isolation also
keeps these tests from dictating import order for anything else that imports
the Flask app.
"""

import unittest

from app_harness import result as _result, run as _run


class TestDefaultModeIsLocal(unittest.TestCase):
    """A container started with no configuration must be usable, and closed.

    This is the issue #13 requirement: no identity provider needed. It is met by
    local accounts rather than by disabling authentication, so the instance is
    never open to whoever reaches the port.
    """

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            from app import app, AUTH_MODE, LOCAL_AUTH
            app.config['TESTING'] = True
            client = app.test_client()

            jobs = client.get('/api/jobs')
            status = client.get('/api/auth/status')
            login = client.get('/login')
            setup = client.get('/setup')

            emit(
                auth_mode=AUTH_MODE,
                local_auth=LOCAL_AUTH,
                jobs_status=jobs.status_code,
                auth_status=status.get_json(),
                login_status=login.status_code,
                login_location=login.headers.get('Location'),
                setup_status=setup.status_code,
            )
        """)
        cls.proc = proc
        cls.res = _result(proc)

    def test_local_is_the_default_mode(self):
        self.assertEqual(self.res['auth_mode'], 'local')
        self.assertTrue(self.res['local_auth'])

    def test_api_still_requires_authentication(self):
        """No mode leaves the API open; Settings holds third-party API keys."""
        self.assertEqual(self.res['jobs_status'], 401)

    def test_status_advertises_mode_and_setup(self):
        status = self.res['auth_status']
        self.assertEqual(status['auth_mode'], 'local')
        self.assertTrue(status['local_auth_enabled'])
        self.assertTrue(status['setup_required'])
        self.assertFalse(status['sso_enabled'])
        self.assertFalse(status['auth_disabled'])

    def test_login_sends_a_fresh_instance_to_setup(self):
        self.assertEqual(self.res['login_status'], 302)
        self.assertIn('/setup', self.res['login_location'])

    def test_setup_page_is_reachable(self):
        self.assertEqual(self.res['setup_status'], 200)


class TestAuthModeNoneIsMigrated(unittest.TestCase):
    """AUTH_MODE=none is gone, but deployments still carrying it must boot.

    They land on setup, and setup adopts the passwordless account that mode
    created so its job history stays attached to the same username.
    """

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            import database
            from app import app, AUTH_MODE

            # Stand in for an instance that previously ran AUTH_MODE=none: the
            # account exists with no password, and owns a job.
            database.ensure_local_user('local')

            app.config['TESTING'] = True
            client = app.test_client()
            status = client.get('/api/auth/status')

            created = client.post('/setup', data={
                'username': 'local',
                'password': 'correct horse battery',
                'confirm_password': 'correct horse battery',
            })
            row = database.get_user('local')
            users = database.get_db_user_count() if hasattr(
                database, 'get_db_user_count') else None

            emit(
                auth_mode=AUTH_MODE,
                setup_required=status.get_json()['setup_required'],
                setup_status=created.status_code,
                adopted_has_password=bool(row and row.get('password_hash')),
                adopted_is_admin=bool(row and row['is_admin']),
                adopted_has_no_oidc_sub=(row or {}).get('oidc_sub') is None,
            )
        """, AUTH_MODE='none')
        cls.proc = proc
        cls.res = _result(proc)

    def test_none_is_accepted_and_treated_as_local(self):
        self.assertEqual(self.res['auth_mode'], 'local')

    def test_it_warns_rather_than_failing_to_boot(self):
        self.assertIn('AUTH_MODE=none is no longer supported', self.proc.stdout)

    def test_the_instance_needs_setup(self):
        self.assertTrue(self.res['setup_required'])

    def test_setup_adopts_the_existing_account(self):
        """Reusing the row keeps history attached; a new username would orphan it."""
        self.assertEqual(self.res['setup_status'], 302)
        self.assertTrue(self.res['adopted_has_password'])
        self.assertTrue(self.res['adopted_is_admin'])
        self.assertTrue(self.res['adopted_has_no_oidc_sub'])


class TestAuthentikMode(unittest.TestCase):
    """AUTH_MODE=authentik keeps the SSO-only behaviour."""

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            from app import app, AUTH_MODE, LOCAL_AUTH
            app.config['TESTING'] = True
            client = app.test_client()

            status = client.get('/api/auth/status')
            jobs = client.get('/api/jobs')
            form_login = client.post('/auth/local/login', data={
                'username': 'someone', 'password': 'whatever-they-typed',
            })
            json_login = client.post('/auth/local/login', json={
                'username': 'someone', 'password': 'whatever-they-typed',
            })
            after = client.get('/api/jobs')

            emit(
                auth_mode=AUTH_MODE,
                local_auth=LOCAL_AUTH,
                auth_status=status.get_json(),
                jobs_status=jobs.status_code,
                form_login_status=form_login.status_code,
                form_login_location=form_login.headers.get('Location'),
                json_login_status=json_login.status_code,
                still_unauthenticated=after.status_code,
            )
        """, AUTH_MODE='authentik',
             AUTHENTIK_CLIENT_ID='cid', AUTHENTIK_CLIENT_SECRET='secret')
        cls.res = _result(proc)

    def test_mode_reported(self):
        self.assertEqual(self.res['auth_mode'], 'authentik')
        self.assertFalse(self.res['local_auth'])

    def test_no_setup_needed(self):
        """Accounts arrive from the provider, so there is nothing to bootstrap."""
        status = self.res['auth_status']
        self.assertFalse(status['setup_required'])
        self.assertFalse(status['local_auth_enabled'])
        self.assertTrue(status['sso_enabled'])

    def test_api_protected(self):
        self.assertEqual(self.res['jobs_status'], 401)

    def test_password_login_is_refused(self):
        """Otherwise SSO could be bypassed by any account holding a password."""
        # A browser gets sent back to the login page; the SPA gets a status.
        self.assertEqual(self.res['form_login_status'], 302)
        self.assertIn('/login', self.res['form_login_location'])
        self.assertEqual(self.res['json_login_status'], 400)
        # Above all: no session was established either way.
        self.assertEqual(self.res['still_unauthenticated'], 401)


class TestInvalidAuthMode(unittest.TestCase):
    def test_unknown_mode_fails_loudly_at_boot(self):
        proc = _run("""
            import app  # noqa: F401
            emit(ok=True)
        """, AUTH_MODE='disabled')
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn('AUTH_MODE', proc.stderr)
        self.assertIn('local', proc.stderr)


if __name__ == '__main__':
    unittest.main()
