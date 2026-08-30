"""Tests for mobile JWT authentication (Wave 1 of the Android app plan).

Covers:
- JWT infrastructure (issue/verify, type separation, expiry)
- GET /api/mobile/auth/login-url
- Mobile OIDC callback branch issuing a deep-link redirect
- POST /api/mobile/auth/refresh
- GET /api/mobile/me
- Dual-auth: Bearer tokens accepted on existing protected endpoints
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir
os.environ.setdefault('JWT_SECRET_KEY', 'unit-test-secret-key')
os.environ.setdefault('AUTHENTIK_CLIENT_ID', 'test-client-id')
os.environ.setdefault('AUTHENTIK_CLIENT_SECRET', 'test-client-secret')
os.environ.setdefault('AUTHENTIK_ISSUER_URL', 'https://auth.example.test/application/o/pick-a-recipe')

_META = {
    'authorization_endpoint': 'https://auth.example.test/authorize',
    'token_endpoint': 'https://auth.example.test/token',
    'userinfo_endpoint': 'https://auth.example.test/userinfo',
    'end_session_endpoint': 'https://auth.example.test/logout',
}

_USERINFO = {
    'sub': 'mobile-sub-1',
    'preferred_username': 'mobile_user',
    'email': 'mobile@example.com',
    'name': 'Mobile User',
    'groups': ['pick-a-recipe-users'],
}


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class MobileAuthTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from database import init_db
        init_db()
        import app as app_module
        cls.app_module = app_module
        cls.mobile_auth = app_module.mobile_auth
        app_module.app.config['TESTING'] = True
        cls.factory = app_module.app.test_client

    def _client(self):
        return self.factory()

    def _issue_pair(self, username='bearer_user'):
        from database import upsert_oidc_user, get_user
        if not get_user(username):
            upsert_oidc_user(
                sub=f'{username}-sub', username=username,
                email=f'{username}@example.com', name=username.title(),
                avatar_url=None, is_admin=False,
            )
        return self.mobile_auth.issue_token_pair(username, is_admin=False)


class TestJwtInfrastructure(MobileAuthTestCase):
    def test_issue_and_decode_access(self):
        pair = self._issue_pair()
        payload = self.mobile_auth.decode_token(pair['access_token'], expected_type='access')
        self.assertEqual(payload['sub'], 'bearer_user')

    def test_refresh_token_rejected_as_access(self):
        pair = self._issue_pair()
        self.assertIsNone(self.mobile_auth.decode_token(pair['refresh_token'], expected_type='access'))

    def test_expired_token_rejected(self):
        original = self.mobile_auth.ACCESS_TTL_SECONDS
        self.mobile_auth.ACCESS_TTL_SECONDS = -10
        try:
            pair = self._issue_pair()
        finally:
            self.mobile_auth.ACCESS_TTL_SECONDS = original
        self.assertIsNone(self.mobile_auth.decode_token(pair['access_token'], expected_type='access'))

    def test_tampered_signature_rejected(self):
        pair = self._issue_pair()
        tampered = pair['access_token'][:-3] + ('aaa' if not pair['access_token'].endswith('aaa') else 'bbb')
        self.assertIsNone(self.mobile_auth.decode_token(tampered, expected_type='access'))

    def test_garbage_rejected(self):
        self.assertIsNone(self.mobile_auth.decode_token('not-a-jwt', expected_type='access'))


class TestMobileEndpoints(MobileAuthTestCase):
    def test_login_url_shape(self):
        client = self._client()
        with mock.patch.object(self.app_module.oauth.authentik, 'load_server_metadata', return_value=_META):
            resp = client.get('/api/mobile/auth/login-url?redirect=par://auth/callback')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn(_META['authorization_endpoint'], body['auth_url'])
        self.assertIn('state=', body['auth_url'])
        self.assertIn('client_id=test-client-id', body['auth_url'])

    def test_login_url_rejects_foreign_scheme(self):
        client = self._client()
        resp = client.get('/api/mobile/auth/login-url?redirect=pap://auth/callback')
        self.assertEqual(resp.status_code, 400)

    def test_refresh_happy_path(self):
        client = self._client()
        pair = self._issue_pair()
        resp = client.post('/api/mobile/auth/refresh', json={'refresh_token': pair['refresh_token']})
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertIn('access_token', body)
        self.assertIsNotNone(
            self.mobile_auth.decode_token(body['access_token'], expected_type='access'))

    def test_refresh_with_garbage_returns_401(self):
        client = self._client()
        resp = client.post('/api/mobile/auth/refresh', json={'refresh_token': 'garbage'})
        self.assertEqual(resp.status_code, 401)

    def test_me_requires_credentials(self):
        client = self._client()
        resp = client.get('/api/mobile/me')
        self.assertEqual(resp.status_code, 401)

    def test_me_with_bearer(self):
        client = self._client()
        pair = self._issue_pair()
        resp = client.get('/api/mobile/me', headers={'Authorization': f"Bearer {pair['access_token']}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json().get('username'), 'bearer_user')

    def test_dual_auth_jobs_list_with_bearer(self):
        client = self._client()
        pair = self._issue_pair()
        resp = client.get('/api/jobs', headers={'Authorization': f"Bearer {pair['access_token']}"})
        self.assertEqual(resp.status_code, 200)

    def test_refresh_token_not_accepted_as_bearer_on_api(self):
        client = self._client()
        pair = self._issue_pair()
        resp = client.get('/api/jobs', headers={'Authorization': f"Bearer {pair['refresh_token']}"})
        self.assertEqual(resp.status_code, 401)


class TestMobileCallback(MobileAuthTestCase):
    def _save_nonce(self, redirect='par://auth/callback'):
        import secrets
        from database import save_mobile_nonce
        nonce = secrets.token_urlsafe(32)
        save_mobile_nonce(nonce, redirect)
        return nonce

    def test_callback_redirects_to_deep_link_with_tokens(self):
        client = self._client()
        nonce = self._save_nonce()
        token_payload = {
            'access_token': 'idp-access-token',
            'token_type': 'Bearer',
            'id_token': 'fake-id-token',
        }
        with mock.patch.object(self.app_module.requests, 'post', return_value=_FakeResponse(token_payload)), \
             mock.patch.object(self.app_module.requests, 'get', return_value=_FakeResponse(_USERINFO)):
            resp = client.get(f'/auth/callback?code=real-code&state={nonce}')
        self.assertEqual(resp.status_code, 302)
        location = resp.headers['Location']
        self.assertTrue(location.startswith('par://auth/callback#access_token='))
        self.assertIn('refresh_token=', location)
        fragment = location.split('#', 1)[1]
        from urllib.parse import parse_qs
        claims = parse_qs(fragment)
        self.assertIsNotNone(self.mobile_auth.decode_token(claims['access_token'][0], expected_type='access'))
        self.assertIsNotNone(self.mobile_auth.decode_token(claims['refresh_token'][0], expected_type='refresh'))

    def test_idp_error_travels_back_over_the_deep_link(self):
        """An error during app sign-in must not land on the web login page.

        Redirecting to /login would leave the user stranded in a browser while
        the app waits indefinitely, having been told nothing.
        """
        client = self._client()
        nonce = self._save_nonce()
        resp = client.get(f'/auth/callback?error=access_denied&state={nonce}')
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.headers['Location'],
            'par://auth/callback#error=access_denied',
        )

    def test_unrecognised_idp_error_is_not_reflected(self):
        """Only RFC 6749 codes pass through; anything else is reported generically."""
        client = self._client()
        nonce = self._save_nonce()
        resp = client.get(
            f'/auth/callback?error=%3Cscript%3Ealert(1)%3C/script%3E&state={nonce}'
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            resp.headers['Location'],
            'par://auth/callback#error=server_error',
        )

    def test_web_flow_errors_still_flash_and_redirect(self):
        """The browser flow keeps its existing behaviour when there is no nonce."""
        client = self._client()
        resp = client.get('/auth/callback?error=access_denied&state=not-a-nonce')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers['Location'])

    def test_nonce_is_single_use(self):
        client = self._client()
        nonce = self._save_nonce()
        token_payload = {'access_token': 'idp-access-token', 'token_type': 'Bearer'}
        with mock.patch.object(self.app_module.requests, 'post', return_value=_FakeResponse(token_payload)), \
             mock.patch.object(self.app_module.requests, 'get', return_value=_FakeResponse(_USERINFO)):
            first = client.get(f'/auth/callback?code=real-code&state={nonce}')
        self.assertEqual(first.status_code, 302)
        second = client.get(f'/auth/callback?code=real-code&state={nonce}')
        self.assertEqual(second.status_code, 400)

    def test_unknown_state_falls_through_to_web_flow_error(self):
        client = self._client()
        token_payload = {'access_token': 'x'}
        with mock.patch.object(self.app_module.requests, 'post', return_value=_FakeResponse(token_payload)):
            resp = client.get('/auth/callback?code=x&state=not-a-nonce')
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(resp.headers['Location'].startswith('par://'))


if __name__ == '__main__':
    unittest.main()
