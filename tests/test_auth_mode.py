"""Tests for AUTH_MODE — local password accounts versus OIDC single sign-on.

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


class TestOidcMode(unittest.TestCase):
    """AUTH_MODE=oidc keeps the SSO-only behaviour."""

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
        """, AUTH_MODE='oidc', OIDC_ISSUER_URL='https://idp.example.test',
             OIDC_CLIENT_ID='cid', OIDC_CLIENT_SECRET='secret',
             OIDC_PROVIDER_NAME='Keycloak')
        cls.res = _result(proc)

    def test_mode_reported(self):
        self.assertEqual(self.res['auth_mode'], 'oidc')
        self.assertFalse(self.res['local_auth'])

    def test_provider_name_is_reported_for_the_sign_in_button(self):
        """So clients say "Sign in with Keycloak", not the name of whichever
        provider this app happened to be written against."""
        self.assertEqual(self.res['auth_status']['sso_provider_name'], 'Keycloak')

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


class TestLegacyAuthentikNamesStillWork(unittest.TestCase):
    """Issue #37: the rename must not break a working Authentik deployment.

    An operator who set AUTH_MODE=authentik and the AUTHENTIK_* variables gets
    the same behaviour under the new names, without editing anything.
    """

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            import app as app_module
            from app import AUTH_MODE, LOCAL_AUTH, OIDC_ISSUER_URL, OIDC_USER_GROUP
            app_module.app.config['TESTING'] = True
            status = app_module.app.test_client().get('/api/auth/status')
            emit(
                auth_mode=AUTH_MODE,
                local_auth=LOCAL_AUTH,
                issuer=OIDC_ISSUER_URL,
                user_group=OIDC_USER_GROUP,
                auth_status=status.get_json(),
            )
        """, AUTH_MODE='authentik',
             AUTHENTIK_ISSUER_URL='https://legacy.example.test',
             AUTHENTIK_CLIENT_ID='cid', AUTHENTIK_CLIENT_SECRET='secret',
             AUTHENTIK_USER_GROUP='old-group')
        cls.res = _result(proc)

    def test_legacy_mode_maps_onto_oidc(self):
        self.assertEqual(self.res['auth_mode'], 'oidc')
        self.assertFalse(self.res['local_auth'])

    def test_legacy_variables_still_configure_the_client(self):
        self.assertEqual(self.res['issuer'], 'https://legacy.example.test')
        self.assertEqual(self.res['user_group'], 'old-group')
        self.assertTrue(self.res['auth_status']['sso_enabled'])


class TestComposePassesEmptyVariables(unittest.TestCase):
    """The upgrade path through Docker Compose, which is where this bites.

    Compose hands every variable declared in the stack file to the container,
    set or not. An upgraded stack therefore passes OIDC_CLIENT_ID="" next to a
    real AUTHENTIK_CLIENT_ID, and treating that empty string as "configured"
    would fail single sign-on closed on the deploy.
    """

    @classmethod
    def setUpClass(cls):
        proc = _run("""
            from app import (
                AUTH_MODE, OIDC_CLIENT_ID, OIDC_ISSUER_URL, OIDC_USER_GROUP,
                oidc_client,
            )
            emit(
                auth_mode=AUTH_MODE,
                client_id=OIDC_CLIENT_ID,
                issuer=OIDC_ISSUER_URL,
                user_group=OIDC_USER_GROUP,
                sso_enabled=oidc_client is not None,
            )
        """, AUTH_MODE='oidc',
             OIDC_ISSUER_URL='', OIDC_CLIENT_ID='', OIDC_CLIENT_SECRET='',
             AUTHENTIK_ISSUER_URL='https://legacy.example.test',
             AUTHENTIK_CLIENT_ID='cid', AUTHENTIK_CLIENT_SECRET='secret')
        cls.res = _result(proc)

    def test_an_empty_new_variable_yields_to_a_set_old_one(self):
        self.assertEqual(self.res['client_id'], 'cid')
        self.assertEqual(self.res['issuer'], 'https://legacy.example.test')
        self.assertTrue(self.res['sso_enabled'])

    def test_an_empty_group_still_means_admit_everyone(self):
        """Empty is meaningful for a group, unlike for a client id."""
        res = _result(_run("""
            from app import OIDC_USER_GROUP, _resolve_oidc_identity
            emit(user_group=OIDC_USER_GROUP,
                 identity=_resolve_oidc_identity(
                     {'sub': 'a', 'preferred_username': 'ada'})[1:])
        """, AUTH_MODE='oidc', OIDC_ISSUER_URL='https://idp.example.test',
             OIDC_CLIENT_ID='cid', OIDC_CLIENT_SECRET='secret',
             OIDC_USER_GROUP='', AUTHENTIK_USER_GROUP='old-group'))
        self.assertEqual(res['user_group'], '')
        self.assertEqual(res['identity'], ['ada', False])


class TestGroupGate(unittest.TestCase):
    """Who _resolve_oidc_identity lets in, across the claim shapes providers use."""

    @classmethod
    def setUpClass(cls):
        cls.res = _result(_run("""
            from app import _resolve_oidc_identity, _claim_groups
            emit(
                list_claim=_resolve_oidc_identity(
                    {'sub': 'a', 'preferred_username': 'ada',
                     'groups': ['pick-a-recipe-users']})[1:],
                admin_claim=_resolve_oidc_identity(
                    {'sub': 'a', 'preferred_username': 'ada',
                     'groups': ['admins']})[1:],
                outsider=_resolve_oidc_identity(
                    {'sub': 'a', 'preferred_username': 'ada',
                     'groups': ['someone-else']})[1:],
                no_groups_at_all=_resolve_oidc_identity(
                    {'sub': 'a', 'preferred_username': 'ada'})[1:],
                space_separated=sorted(_claim_groups({'groups': 'one two'})),
                keycloak_paths=sorted(_claim_groups({'groups': ['/admins']})),
                objects=sorted(_claim_groups({'groups': [{'name': 'devs'}]})),
            )
        """, AUTH_MODE='oidc', OIDC_ISSUER_URL='https://idp.example.test',
             OIDC_CLIENT_ID='cid', OIDC_CLIENT_SECRET='secret'))

    def test_user_group_grants_access(self):
        self.assertEqual(self.res['list_claim'], ['ada', False])

    def test_admin_group_grants_admin(self):
        self.assertEqual(self.res['admin_claim'], ['ada', True])

    def test_other_groups_are_refused(self):
        self.assertEqual(self.res['outsider'], [None, False])

    def test_a_provider_that_sends_no_groups_is_refused_by_default(self):
        self.assertEqual(self.res['no_groups_at_all'], [None, False])

    def test_claim_shapes_providers_actually_send(self):
        self.assertEqual(self.res['space_separated'], ['one', 'two'])
        # Keycloak's path-style groups arrive with a leading slash.
        self.assertEqual(self.res['keycloak_paths'], ['admins'])
        self.assertEqual(self.res['objects'], ['devs'])


class TestEmptyUserGroupAdmitsEveryone(unittest.TestCase):
    """For providers that cannot publish groups at all, per issue #37."""

    def test_no_group_configured_means_any_authenticated_user(self):
        res = _result(_run("""
            from app import _resolve_oidc_identity
            emit(identity=_resolve_oidc_identity(
                {'sub': 'a', 'preferred_username': 'ada'})[1:])
        """, AUTH_MODE='oidc', OIDC_ISSUER_URL='https://idp.example.test',
             OIDC_CLIENT_ID='cid', OIDC_CLIENT_SECRET='secret',
             OIDC_USER_GROUP=''))
        self.assertEqual(res['identity'], ['ada', False])


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
