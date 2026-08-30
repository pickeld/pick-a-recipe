"""Baseline characterization tests for HTTP-layer auth.

Pins CURRENT (cookie-session-only) behavior on unchanged code:
- Protected API endpoints return 401 JSON without a session cookie.
- The same endpoints return 200 with a valid Flask session.
- The PWA login page is reachable unauthenticated.

These tests must stay green before AND after the mobile JWT auth work
(dual-auth must never regress the cookie path).
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir
# These characterize the OIDC cookie-session path, so pin the mode rather than
# inheriting a default. In local mode a fresh instance sends /login to /setup.
os.environ['AUTH_MODE'] = 'authentik'


class TestBaselineHttpAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from database import init_db, upsert_oidc_user
        init_db()
        upsert_oidc_user(
            sub='baseline-sub', username='baseline_user',
            email='baseline@example.com', name='Baseline',
            avatar_url=None, is_admin=False,
        )
        from app import app
        app.config['TESTING'] = True
        cls.app = app

    def _client(self):
        # Fresh client per test: Flask clients persist cookies across requests,
        # which would leak an authenticated session between tests.
        return self.app.test_client()

    def test_protected_api_returns_401_without_session(self):
        resp = self._client().get('/api/jobs')
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.get_json().get('error'), 'Authentication required')

    def test_protected_api_returns_200_with_session(self):
        client = self._client()
        with client.session_transaction() as sess:
            sess['user'] = 'baseline_user'
            sess['is_admin'] = False
        resp = client.get('/api/jobs')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('jobs', resp.get_json())

    def test_login_page_reachable_unauthenticated(self):
        resp = self._client().get('/login')
        self.assertEqual(resp.status_code, 200)

    def test_healthz_public(self):
        resp = self._client().get('/healthz')
        self.assertEqual(resp.status_code, 200)


if __name__ == '__main__':
    unittest.main()
