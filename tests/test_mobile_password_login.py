"""Tests for app sign-in with a local username and password.

The Android app is installed by people running their own instances, so it has to
work against a server with no identity provider. This covers the endpoint that
makes that possible, and the ways it must refuse.

Forked-interpreter approach, as in test_local_auth: AUTH_MODE is fixed when
`app` is imported, and both modes need exercising here.
"""

import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'ui'))

from app_harness import result as _result, run as _run  # noqa: E402

PASSWORD = 'a properly long passphrase'
LOGIN = '/api/mobile/auth/login'
SECRET = 'test-only-signing-key-of-adequate-length-0123456789'

_PRELUDE = f"""
    import database, login_throttle, mobile_auth, passwords
    from app import app

    PASSWORD = {PASSWORD!r}
    LOGIN = {LOGIN!r}
    app.config['TESTING'] = True

    def hashed(password=PASSWORD):
        return passwords.hash_password(password)

    def login(username, password=PASSWORD, client=None):
        return (client or app.test_client()).post(
            LOGIN, json={{'username': username, 'password': password}})
"""


def _run_mobile(script: str, **env) -> dict:
    env.setdefault('JWT_SECRET_KEY', SECRET)
    combined = textwrap.dedent(_PRELUDE) + textwrap.dedent(script)
    return _result(_run(combined, **env))


class TestPasswordLogin(unittest.TestCase):
    """The happy path, and the token pair it has to hand back."""

    @classmethod
    def setUpClass(cls):
        cls.res = _run_mobile("""
            database.claim_first_local_admin('boss', hashed())
            database.create_local_user('cook', hashed(), is_admin=False)

            ok = login('boss')
            body = ok.get_json()
            access = mobile_auth.decode_token(body.get('access_token', ''),
                                              expected_type='access')
            refresh = mobile_auth.decode_token(body.get('refresh_token', ''),
                                               expected_type='refresh')

            # The token must actually open the app's endpoints, not merely decode.
            me = app.test_client().get('/api/mobile/me', headers={
                'Authorization': 'Bearer ' + body['access_token']})

            plain = login('cook').get_json()
            plain_claims = mobile_auth.decode_token(plain['access_token'],
                                                   expected_type='access')

            emit(
                status=ok.status_code,
                token_type=body.get('token_type'),
                expires_in=body.get('expires_in'),
                access_sub=access and access.get('sub'),
                access_admin=access and access.get('adm'),
                refresh_sub=refresh and refresh.get('sub'),
                me_status=me.status_code,
                me_user=me.get_json().get('username'),
                me_admin=me.get_json().get('is_admin'),
                plain_admin=plain_claims.get('adm'),
            )
        """)

    def test_returns_a_usable_token_pair(self):
        self.assertEqual(self.res['status'], 200)
        self.assertEqual(self.res['token_type'], 'Bearer')
        self.assertEqual(self.res['expires_in'], 900)
        self.assertEqual(self.res['access_sub'], 'boss')
        self.assertEqual(self.res['refresh_sub'], 'boss')

    def test_the_token_opens_the_app_endpoints(self):
        self.assertEqual(self.res['me_status'], 200)
        self.assertEqual(self.res['me_user'], 'boss')
        self.assertTrue(self.res['me_admin'])

    def test_admin_rights_are_carried_and_not_invented(self):
        self.assertTrue(self.res['access_admin'])
        self.assertFalse(self.res['plain_admin'])


class TestRefusals(unittest.TestCase):
    """Wrong credentials, and the states where there is nothing to sign into."""

    @classmethod
    def setUpClass(cls):
        cls.res = _run_mobile("""
            before_setup = login('boss')

            database.claim_first_local_admin('boss', hashed())

            wrong = login('boss', 'not the passphrase')
            missing = login('ghost')
            blank = app.test_client().post(LOGIN, json={})
            not_json = app.test_client().post(LOGIN, data='username=boss')

            emit(
                before_setup_status=before_setup.status_code,
                before_setup_flag=before_setup.get_json().get('setup_required'),
                wrong_status=wrong.status_code,
                wrong_error=wrong.get_json().get('error'),
                missing_status=missing.status_code,
                missing_error=missing.get_json().get('error'),
                # Same wording either way, or the endpoint becomes a way to
                # enumerate which accounts exist.
                indistinguishable=(wrong.get_json().get('error')
                                   == missing.get_json().get('error')),
                wrong_has_no_token='access_token' not in wrong.get_json(),
                blank_status=blank.status_code,
                not_json_status=not_json.status_code,
            )
        """)

    def test_before_setup_it_says_so_rather_than_rejecting_credentials(self):
        # An empty instance is not a wrong password, and the app can act on the
        # difference by sending the user to a browser.
        self.assertEqual(self.res['before_setup_status'], 409)
        self.assertTrue(self.res['before_setup_flag'])

    def test_wrong_password_is_refused_without_a_token(self):
        self.assertEqual(self.res['wrong_status'], 401)
        self.assertTrue(self.res['wrong_has_no_token'])

    def test_unknown_and_wrong_are_indistinguishable(self):
        self.assertEqual(self.res['missing_status'], 401)
        self.assertTrue(self.res['indistinguishable'])

    def test_missing_and_malformed_bodies_are_refused(self):
        self.assertEqual(self.res['blank_status'], 401)
        self.assertEqual(self.res['not_json_status'], 401)


class TestThrottling(unittest.TestCase):
    """The app endpoint must not be a way around the form's rate limit."""

    @classmethod
    def setUpClass(cls):
        cls.res = _run_mobile("""
            database.claim_first_local_admin('boss', hashed())
            login_throttle.clear_all()

            statuses = [login('boss', 'wrong').status_code for _ in range(5)]
            throttled = login('boss', 'wrong')

            # Shares the ladder with the browser form, keyed on (IP, username):
            # otherwise an attacker throttled on one could just use the other.
            form = app.test_client().post(
                '/auth/local/login',
                json={'username': 'boss', 'password': PASSWORD})

            login_throttle.clear_all()
            after_reset = login('boss')

            emit(
                statuses=statuses,
                throttled_status=throttled.status_code,
                retry_after=throttled.headers.get('Retry-After'),
                throttled_error=throttled.get_json().get('error'),
                form_status=form.status_code,
                after_reset=after_reset.status_code,
            )
        """)

    def test_repeated_failures_start_being_throttled(self):
        self.assertIn(429, self.res['statuses'] + [self.res['throttled_status']])
        self.assertEqual(self.res['throttled_status'], 429)

    def test_the_wait_is_stated_in_the_body_and_the_header(self):
        # Without it the app can only offer "try again", and the user taps.
        self.assertIsNotNone(self.res['retry_after'])
        self.assertIn('Try again in', self.res['throttled_error'])

    def test_the_browser_form_shares_the_same_ladder(self):
        self.assertEqual(self.res['form_status'], 429)

    def test_a_correct_password_works_once_the_window_passes(self):
        self.assertEqual(self.res['after_reset'], 200)


class TestDisabledSurfaces(unittest.TestCase):
    """Both switches that turn this endpoint off, and how it says so."""

    @classmethod
    def setUpClass(cls):
        cls.without_secret = _run_mobile("""
            database.claim_first_local_admin('boss', hashed())
            resp = login('boss')
            emit(status=resp.status_code,
                 error=resp.get_json().get('error'),
                 enabled=mobile_auth.mobile_auth_enabled())
        """, JWT_SECRET_KEY='')

        cls.under_sso = _run_mobile("""
            database.create_local_user('boss', hashed(), is_admin=True)
            resp = login('boss')
            emit(status=resp.status_code,
                 error=resp.get_json().get('error'),
                 has_token='access_token' in resp.get_json())
        """, AUTH_MODE='authentik',
             AUTHENTIK_CLIENT_ID='cid', AUTHENTIK_CLIENT_SECRET='secret')

    def test_without_a_signing_key_it_is_unavailable(self):
        self.assertFalse(self.without_secret['enabled'])
        self.assertEqual(self.without_secret['status'], 503)
        self.assertIn('JWT_SECRET_KEY', self.without_secret['error'])

    def test_under_authentik_a_password_is_refused(self):
        # Group membership governs access there, not the password an account may
        # happen to carry, so honouring one would go around single sign-on.
        self.assertEqual(self.under_sso['status'], 400)
        self.assertFalse(self.under_sso['has_token'])
        self.assertIn('single sign-on', self.under_sso['error'])


class TestStatusTellsTheAppWhichWayIn(unittest.TestCase):
    """The app reads /api/auth/status before offering a form or a button."""

    @classmethod
    def setUpClass(cls):
        cls.local = _run_mobile("""
            fresh = app.test_client().get('/api/auth/status').get_json()
            database.claim_first_local_admin('boss', hashed())
            ready = app.test_client().get('/api/auth/status').get_json()
            emit(fresh=fresh, ready=ready)
        """)

        cls.sso = _run_mobile("""
            emit(status=app.test_client().get('/api/auth/status').get_json())
        """, AUTH_MODE='authentik',
             AUTHENTIK_CLIENT_ID='cid', AUTHENTIK_CLIENT_SECRET='secret')

    def test_status_is_readable_without_a_session(self):
        # It has to be: it is what the app asks before it has any credential.
        self.assertIsNotNone(self.local['fresh'])

    def test_a_fresh_local_instance_asks_for_setup(self):
        fresh = self.local['fresh']
        self.assertTrue(fresh['setup_required'])
        self.assertTrue(fresh['local_auth_enabled'])
        self.assertFalse(fresh['sso_enabled'])
        self.assertTrue(fresh['mobile_auth_enabled'])

    def test_setup_required_clears_once_an_account_exists(self):
        self.assertFalse(self.local['ready']['setup_required'])

    def test_an_sso_instance_advertises_the_browser_flow_only(self):
        status = self.sso['status']
        self.assertTrue(status['sso_enabled'])
        self.assertFalse(status['local_auth_enabled'])
        self.assertFalse(status['setup_required'])


if __name__ == '__main__':
    unittest.main()
