"""Tests for admin user management, admin gating, and session revocation.

Same forked-interpreter approach as test_local_auth: AUTH_MODE is fixed when
`app` is imported, and other suites in this run need it left alone.
"""

import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'ui'))

from app_harness import result as _result, run as _run  # noqa: E402

PASSWORD = 'a properly long passphrase'
OTHER_PASSWORD = 'another properly long phrase'

# Gives each script an admin, a plain user, and signed-in clients for both.
_PRELUDE = f"""
    import database, login_throttle, passwords
    from app import app

    PASSWORD = {PASSWORD!r}
    OTHER_PASSWORD = {OTHER_PASSWORD!r}
    app.config['TESTING'] = True

    def hashed(password=PASSWORD):
        return passwords.hash_password(password)

    def sign_in(username, password=PASSWORD):
        client = app.test_client()
        resp = client.post('/auth/local/login',
                           json={{'username': username, 'password': password}})
        assert resp.status_code == 200, (username, resp.status_code, resp.data)
        return client

    def seed():
        database.claim_first_local_admin('boss', hashed())
        database.create_local_user('cook', hashed(), is_admin=False)
        return sign_in('boss'), sign_in('cook')
"""


def _run_local(script: str, **env) -> dict:
    combined = textwrap.dedent(_PRELUDE) + textwrap.dedent(script)
    return _result(_run(combined, **env))


class TestConfigIsAdminOnly(unittest.TestCase):
    """Settings holds the LLM, Mealie and Tandoor API keys.

    Reading it is as sensitive as writing it, so a non-admin must not be able to
    GET it either — that was the hole that made admin-created accounts unsafe.
    """

    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            emit(
                admin_get=admin.get('/api/config').status_code,
                user_get=user.get('/api/config').status_code,
                user_post=user.post('/api/config',
                                    json={'recipe_language': 'de'}).status_code,
                user_export=user.get('/api/settings/export').status_code,
                user_import=user.post('/api/settings/import',
                                      json={'settings': {}}).status_code,
                user_cookies_delete=user.delete('/api/cookies/delete').status_code,
                anon_get=app.test_client().get('/api/config').status_code,
                # The keys must not have moved despite the attempt.
                language_unchanged=database.load_config().get(
                    'recipe_language') != 'de',
            )
        """)

    def test_admin_can_read_config(self):
        self.assertEqual(self.res['admin_get'], 200)

    def test_non_admin_cannot_read_the_api_keys(self):
        self.assertEqual(self.res['user_get'], 403)
        self.assertEqual(self.res['user_export'], 403)

    def test_non_admin_cannot_change_instance_config(self):
        self.assertEqual(self.res['user_post'], 403)
        self.assertEqual(self.res['user_import'], 403)
        self.assertEqual(self.res['user_cookies_delete'], 403)
        self.assertTrue(self.res['language_unchanged'])

    def test_anonymous_gets_401_not_403(self):
        """So a client can tell "sign in" from "you are not allowed"."""
        self.assertEqual(self.res['anon_get'], 401)


class TestListingAndCreating(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            listing = admin.get('/api/users')
            body = listing.get_json()

            created = admin.post('/api/users', json={
                'username': 'baker', 'password': OTHER_PASSWORD, 'is_admin': True,
            })
            duplicate = admin.post('/api/users', json={
                'username': 'baker', 'password': OTHER_PASSWORD,
            })
            weak = admin.post('/api/users', json={
                'username': 'weakling', 'password': 'short',
            })
            nameless = admin.post('/api/users', json={
                'username': '  ', 'password': OTHER_PASSWORD,
            })
            # No spaces, so this can only be rejected by the control-character
            # check rather than incidentally by the whitespace one.
            forged_log = admin.post('/api/users', json={
                'username': 'mole\\nauth-INFO-account_deleted:boss',
                'password': OTHER_PASSWORD,
            })
            tabbed = admin.post('/api/users', json={
                'username': 'mole\\tboss', 'password': OTHER_PASSWORD,
            })
            spaced = admin.post('/api/users', json={
                'username': 'two words', 'password': OTHER_PASSWORD,
            })

            by_user = user.post('/api/users', json={
                'username': 'sneaky', 'password': OTHER_PASSWORD,
            })

            emit(
                listing_status=listing.status_code,
                usernames=[u['username'] for u in body['users']],
                admin_count=body['admin_count'],
                hashes_absent=all(
                    'password_hash' not in u for u in body['users']),
                has_password_flags={
                    u['username']: u['has_password'] for u in body['users']},
                listing_by_non_admin=user.get('/api/users').status_code,
                created_status=created.status_code,
                created_is_admin=created.get_json()['user']['is_admin'],
                new_user_can_sign_in=app.test_client().post(
                    '/auth/local/login',
                    json={'username': 'baker', 'password': OTHER_PASSWORD},
                ).status_code,
                duplicate_status=duplicate.status_code,
                weak_status=weak.status_code,
                nameless_status=nameless.status_code,
                forged_log_status=forged_log.status_code,
                tabbed_status=tabbed.status_code,
                spaced_status=spaced.status_code,
                forged_account_absent=not any(
                    'account_deleted' in u['username']
                    for u in admin.get('/api/users').get_json()['users']),
                by_user_status=by_user.status_code,
                sneaky_exists=database.get_user('sneaky') is not None,
            )
        """)

    def test_listing_shows_accounts_without_hashes(self):
        self.assertEqual(self.res['listing_status'], 200)
        self.assertEqual(sorted(self.res['usernames']), ['boss', 'cook'])
        self.assertEqual(self.res['admin_count'], 1)
        self.assertTrue(
            self.res['hashes_absent'],
            'the API must never hand out password hashes',
        )
        self.assertEqual(
            self.res['has_password_flags'], {'boss': True, 'cook': True}
        )

    def test_only_admins_can_list(self):
        self.assertEqual(self.res['listing_by_non_admin'], 403)

    def test_admin_can_create_a_usable_account(self):
        self.assertEqual(self.res['created_status'], 201)
        self.assertTrue(self.res['created_is_admin'])
        self.assertEqual(self.res['new_user_can_sign_in'], 200)

    def test_duplicate_username_is_refused(self):
        self.assertEqual(self.res['duplicate_status'], 409)

    def test_weak_password_and_blank_name_refused(self):
        self.assertEqual(self.res['weak_status'], 400)
        self.assertEqual(self.res['nameless_status'], 400)

    def test_control_characters_in_a_username_are_refused(self):
        """A newline would let a created account forge account-audit entries."""
        self.assertEqual(self.res['forged_log_status'], 400)
        self.assertEqual(self.res['tabbed_status'], 400)
        self.assertEqual(self.res['spaced_status'], 400)
        self.assertTrue(self.res['forged_account_absent'])

    def test_non_admin_cannot_create_accounts(self):
        """No self-registration: the admin decides who exists."""
        self.assertEqual(self.res['by_user_status'], 403)
        self.assertFalse(self.res['sneaky_exists'])


class TestAdminRightsGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            promote = admin.patch('/api/users/cook', json={'is_admin': True})
            cook_admin_after_promote = bool(
                database.get_user('cook')['is_admin'])

            # Two admins now, so demoting the other one is allowed...
            demote_other = admin.patch('/api/users/cook',
                                       json={'is_admin': False})
            # ...but never yourself, and never the last one standing.
            demote_self = admin.patch('/api/users/boss',
                                      json={'is_admin': False})
            boss_still_admin = bool(database.get_user('boss')['is_admin'])

            missing = admin.patch('/api/users/ghost', json={'is_admin': True})

            # Leave boss as the sole admin and have it try to demote itself
            # through the last-admin guard rather than the self guard.
            database.set_user_admin('cook', True)
            database.set_user_admin('boss', False)
            cook = sign_in('cook')
            demote_last = cook.patch('/api/users/cook', json={'is_admin': False})

            emit(
                promote_status=promote.status_code,
                cook_admin_after_promote=cook_admin_after_promote,
                demote_other_status=demote_other.status_code,
                demote_self_status=demote_self.status_code,
                demote_self_error=(demote_self.get_json() or {}).get('error', ''),
                boss_still_admin=boss_still_admin,
                demote_last_status=demote_last.status_code,
                missing_status=missing.status_code,
            )
        """)

    def test_admin_can_promote_and_demote_others(self):
        self.assertEqual(self.res['promote_status'], 200)
        self.assertTrue(self.res['cook_admin_after_promote'])
        self.assertEqual(self.res['demote_other_status'], 200)

    def test_you_cannot_demote_yourself(self):
        """You would lose the rights needed to undo it."""
        self.assertEqual(self.res['demote_self_status'], 400)
        self.assertIn('your own', self.res['demote_self_error'])
        self.assertTrue(self.res['boss_still_admin'])

    def test_the_last_admin_cannot_be_demoted(self):
        self.assertEqual(self.res['demote_last_status'], 400)

    def test_unknown_account_is_404(self):
        self.assertEqual(self.res['missing_status'], 404)


class TestDeletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            # Work owned by the account about to be deleted, plus a job whose
            # pending upload predates ownership being recorded.
            owned = database.create_job('https://example.com/a', user_id='cook')
            legacy = database.create_job('https://example.com/b', user_id='cook')
            keep = database.create_job('https://example.com/c', user_id='boss')
            def upload(upload_id, job_id, owner):
                database.create_pending_upload(
                    upload_id, job_id, {'name': upload_id}, None, [], 'mealie',
                    user_id=owner)

            upload('up-owned', owned, 'cook')
            upload('up-legacy', legacy, None)
            upload('up-keep', keep, 'boss')
            database.save_push_subscription('cook', 'https://p/1', 'k', 'a')

            self_delete = admin.delete('/api/users/boss')
            missing = admin.delete('/api/users/ghost')
            by_user = user.delete('/api/users/boss')

            deleted = admin.delete('/api/users/cook')
            cook_gone = database.get_user('cook') is None

            with database.get_db() as conn:
                remaining_jobs = [
                    r['id'] for r in conn.execute(
                        'SELECT id FROM recipe_jobs').fetchall()]
                remaining_uploads = [
                    r['id'] for r in conn.execute(
                        'SELECT id FROM pending_uploads').fetchall()]
                push_left = conn.execute(
                    'SELECT COUNT(*) FROM push_subscriptions '
                    'WHERE username = ?', ('cook',)).fetchone()[0]

            # Reusing the name must not inherit the previous holder's work.
            database.create_local_user('cook', hashed(), is_admin=False)
            reused = sign_in('cook')
            inherited = reused.get('/api/jobs').get_json()

            emit(
                self_delete_status=self_delete.status_code,
                self_delete_error=(self_delete.get_json() or {}).get('error', ''),
                missing_status=missing.status_code,
                by_user_status=by_user.status_code,
                boss_survives=database.get_user('boss') is not None,
                deleted_status=deleted.status_code,
                cook_gone=cook_gone,
                kept_job_present=keep in remaining_jobs,
                owned_jobs_gone=owned not in remaining_jobs
                                and legacy not in remaining_jobs,
                kept_upload_present='up-keep' in remaining_uploads,
                owned_uploads_gone='up-owned' not in remaining_uploads,
                legacy_upload_gone='up-legacy' not in remaining_uploads,
                push_left=push_left,
                inherited_job_count=len(inherited.get('jobs', inherited)
                                        if isinstance(inherited, dict)
                                        else inherited),
            )
        """)

    def test_you_cannot_delete_your_own_account(self):
        self.assertEqual(self.res['self_delete_status'], 400)
        self.assertIn('your own', self.res['self_delete_error'])
        self.assertTrue(self.res['boss_survives'])

    def test_unknown_account_and_non_admin_refused(self):
        self.assertEqual(self.res['missing_status'], 404)
        self.assertEqual(self.res['by_user_status'], 403)

    def test_deleting_removes_the_account(self):
        self.assertEqual(self.res['deleted_status'], 200)
        self.assertTrue(self.res['cook_gone'])

    def test_owned_work_goes_with_the_account(self):
        """Ownership is the username string, so orphans would be inheritable."""
        self.assertTrue(self.res['owned_jobs_gone'])
        self.assertTrue(self.res['owned_uploads_gone'])
        self.assertEqual(self.res['push_left'], 0)

    def test_a_pending_upload_with_no_owner_follows_its_job(self):
        """Rows from before ownership was recorded carry a NULL user_id."""
        self.assertTrue(self.res['legacy_upload_gone'])

    def test_other_accounts_are_untouched(self):
        self.assertTrue(self.res['kept_job_present'])
        self.assertTrue(self.res['kept_upload_present'])

    def test_reusing_the_username_inherits_nothing(self):
        self.assertEqual(self.res['inherited_job_count'], 0)


class TestSessionRevocation(unittest.TestCase):
    """A signed cookie must not outlive the account it names."""

    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            before = user.get('/api/jobs').status_code
            admin.delete('/api/users/cook')
            after = user.get('/api/jobs').status_code

            # Demotion has to bite immediately too, or a demoted admin keeps
            # reading the API keys until their cookie expires.
            database.create_local_user('second', hashed(), is_admin=True)
            other_admin = sign_in('second')
            admin_before = other_admin.get('/api/config').status_code
            admin.patch('/api/users/second', json={'is_admin': False})
            admin_after = other_admin.get('/api/config').status_code
            me_after = other_admin.get('/api/me').get_json()

            emit(
                before=before, after=after,
                admin_before=admin_before, admin_after=admin_after,
                me_is_admin_after=me_after['is_admin'],
            )
        """)

    def test_deleted_account_loses_access_at_once(self):
        self.assertEqual(self.res['before'], 200)
        self.assertEqual(self.res['after'], 401)

    def test_demotion_applies_to_a_live_session(self):
        self.assertEqual(self.res['admin_before'], 200)
        self.assertEqual(self.res['admin_after'], 403)

    def test_api_me_reports_the_current_rights(self):
        """The UI keys admin-only sections off this, so it must not go stale."""
        self.assertFalse(self.res['me_is_admin_after'])


class TestChangeOwnPassword(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            wrong = user.post('/api/me/password', json={
                'current_password': 'not the password',
                'new_password': OTHER_PASSWORD,
            })
            login_throttle.clear_all()

            weak = user.post('/api/me/password', json={
                'current_password': PASSWORD, 'new_password': 'short',
            })

            changed = user.post('/api/me/password', json={
                'current_password': PASSWORD, 'new_password': OTHER_PASSWORD,
            })

            login_throttle.clear_all()
            old_login = app.test_client().post('/auth/local/login',
                json={'username': 'cook', 'password': PASSWORD})
            login_throttle.clear_all()
            new_login = app.test_client().post('/auth/local/login',
                json={'username': 'cook', 'password': OTHER_PASSWORD})

            still_signed_in = user.get('/api/jobs').status_code

            anon = app.test_client().post('/api/me/password', json={
                'current_password': PASSWORD, 'new_password': OTHER_PASSWORD,
            })

            emit(
                wrong_status=wrong.status_code,
                weak_status=weak.status_code,
                changed_status=changed.status_code,
                old_login=old_login.status_code,
                new_login=new_login.status_code,
                still_signed_in=still_signed_in,
                anon_status=anon.status_code,
            )
        """)

    def test_the_current_password_is_required(self):
        """Otherwise a borrowed session could lock the owner out."""
        self.assertEqual(self.res['wrong_status'], 403)

    def test_the_new_password_must_meet_the_policy(self):
        self.assertEqual(self.res['weak_status'], 400)

    def test_changing_it_replaces_the_old_one(self):
        self.assertEqual(self.res['changed_status'], 200)
        self.assertEqual(self.res['old_login'], 401)
        self.assertEqual(self.res['new_login'], 200)

    def test_the_session_that_changed_it_stays_signed_in(self):
        self.assertEqual(self.res['still_signed_in'], 200)

    def test_anonymous_callers_are_refused(self):
        self.assertEqual(self.res['anon_status'], 401)


class TestChangePasswordThrottling(unittest.TestCase):
    """Guessing the current password is a credential attack like any other."""

    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            admin, user = seed()

            statuses = []
            for _ in range(5):
                statuses.append(user.post('/api/me/password', json={
                    'current_password': 'wrong guess here',
                    'new_password': OTHER_PASSWORD,
                }).status_code)

            emit(statuses=statuses)
        """)

    def test_repeated_wrong_guesses_get_throttled(self):
        self.assertIn(429, self.res['statuses'])


class TestAuthentikModeRefusesUserAdmin(unittest.TestCase):
    """Under SSO the identity provider owns accounts, so local CRUD is a lie.

    Creating an account here could not sign in (password login is refused), and
    deleting one would not keep anyone out, since the next OIDC login recreates
    it. Better to say so than to offer controls that do nothing.
    """

    @classmethod
    def setUpClass(cls):
        cls.res = _result(_run(textwrap.dedent(f"""
            import database
            from app import app
            app.config['TESTING'] = True

            database.upsert_oidc_user(sub='sub-1', username='boss', is_admin=True)
            client = app.test_client()
            with client.session_transaction() as sess:
                sess['user'] = 'boss'
                sess['is_admin'] = True

            listed = client.get('/api/users')
            created = client.post('/api/users', json={{
                'username': 'nope', 'password': {PASSWORD!r},
            }})
            patched = client.patch('/api/users/boss', json={{'is_admin': False}})
            deleted = client.delete('/api/users/boss')
            own_password = client.post('/api/me/password', json={{
                'current_password': 'x', 'new_password': {PASSWORD!r},
            }})

            emit(
                listed_status=listed.status_code,
                created_status=created.status_code,
                patched_status=patched.status_code,
                deleted_status=deleted.status_code,
                own_password_status=own_password.status_code,
                boss_survives=database.get_user('boss') is not None,
            )
        """), AUTH_MODE='authentik',
            AUTHENTIK_CLIENT_ID='id', AUTHENTIK_CLIENT_SECRET='secret'))

    def test_listing_still_works_for_visibility(self):
        self.assertEqual(self.res['listed_status'], 200)

    def test_mutations_are_refused(self):
        self.assertEqual(self.res['created_status'], 400)
        self.assertEqual(self.res['patched_status'], 400)
        self.assertEqual(self.res['deleted_status'], 400)
        self.assertTrue(self.res['boss_survives'])

    def test_own_password_change_is_refused(self):
        self.assertEqual(self.res['own_password_status'], 400)


if __name__ == '__main__':
    unittest.main()
