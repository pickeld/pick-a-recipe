"""Tests for local username and password accounts.

Split in two. The hashing tests touch only `passwords`, so they run in-process.
Everything that needs the Flask app runs in a forked interpreter via
app_harness: importing `app` fixes AUTH_MODE for the whole process, and the
SSO suites in this same run need it left on `authentik`.

One fork per class rather than per test, so the cost of importing the app is
paid a handful of times instead of thirty.
"""

import os
import sys
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'ui'))

from app_harness import result as _result, run as _run  # noqa: E402

import passwords  # noqa: E402

GOOD_PASSWORD = 'a properly long passphrase'

# Prelude shared by the forked scripts: local mode, an empty users table, and a
# helper that creates the first account without going through the HTTP flow.
_PRELUDE = f"""
    import database, login_throttle, passwords
    from app import app

    GOOD_PASSWORD = {GOOD_PASSWORD!r}
    app.config['TESTING'] = True

    def make_admin(username='admin', password=GOOD_PASSWORD):
        return database.claim_first_local_admin(
            username, passwords.hash_password(password)
        )
"""


def _run_local(script: str, **env) -> dict:
    # Dedented separately: the two literals are indented differently, and
    # dedent only strips the indent common to everything it is given.
    combined = textwrap.dedent(_PRELUDE) + textwrap.dedent(script)
    return _result(_run(combined, **env))


class TestPasswordHashing(unittest.TestCase):
    """Pure hashing behaviour; no Flask app involved."""

    def test_hash_is_argon2id_and_salted(self):
        first = passwords.hash_password(GOOD_PASSWORD)
        second = passwords.hash_password(GOOD_PASSWORD)
        self.assertTrue(first.startswith('$argon2id$'))
        # Distinct salts, so identical passwords do not share a hash and a
        # stolen database cannot be scanned for repeats.
        self.assertNotEqual(first, second)

    def test_verify_accepts_the_password_and_rejects_others(self):
        stored = passwords.hash_password(GOOD_PASSWORD)
        self.assertTrue(passwords.verify(stored, GOOD_PASSWORD))
        self.assertFalse(passwords.verify(stored, GOOD_PASSWORD + 'x'))
        self.assertFalse(passwords.verify(stored, ''))

    def test_verify_is_false_for_accounts_without_a_password(self):
        """An OIDC or admin-created row must not be signable-into."""
        self.assertFalse(passwords.verify(None, GOOD_PASSWORD))
        self.assertFalse(passwords.verify('', GOOD_PASSWORD))

    def test_verify_survives_a_corrupt_hash(self):
        self.assertFalse(passwords.verify('not-a-hash', GOOD_PASSWORD))

    def test_length_policy(self):
        with self.assertRaises(passwords.WeakPassword):
            passwords.hash_password('short')
        with self.assertRaises(passwords.WeakPassword):
            passwords.hash_password('')
        with self.assertRaises(passwords.WeakPassword):
            passwords.hash_password('x' * (passwords.MAX_PASSWORD_LENGTH + 1))

    def test_no_composition_rules(self):
        """Length is the requirement; a long lowercase phrase is fine."""
        self.assertTrue(passwords.hash_password('all lowercase words here'))

    def test_rehash_detects_outdated_parameters(self):
        from argon2 import PasswordHasher
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
            GOOD_PASSWORD
        )
        self.assertTrue(passwords.needs_rehash(weak))
        self.assertFalse(passwords.needs_rehash(passwords.hash_password(GOOD_PASSWORD)))


class TestFirstRunSetup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            client = app.test_client()
            page = client.get('/setup')

            created = client.post('/setup', data={
                'username': 'chef',
                'password': GOOD_PASSWORD,
                'confirm_password': GOOD_PASSWORD,
            })
            row = database.get_user('chef')
            authed_after_setup = client.get('/api/jobs').status_code

            # Setup must now be closed to everyone, including a second visitor.
            other = app.test_client()
            reget = other.get('/setup')
            usurp = other.post('/setup', data={
                'username': 'usurper',
                'password': GOOD_PASSWORD,
                'confirm_password': GOOD_PASSWORD,
            })

            emit(
                page_status=page.status_code,
                page_has_form=b'Create account' in page.data,
                created_status=created.status_code,
                row_is_admin=bool(row and row['is_admin']),
                row_hash_is_argon2id=(row or {}).get(
                    'password_hash', '').startswith('$argon2id$'),
                authed_after_setup=authed_after_setup,
                reget_status=reget.status_code,
                reget_location=reget.headers.get('Location'),
                usurp_status=usurp.status_code,
                usurper_created=database.get_user('usurper') is not None,
            )
        """)

    def test_setup_page_served_while_no_account_exists(self):
        self.assertEqual(self.res['page_status'], 200)
        self.assertTrue(self.res['page_has_form'])

    def test_setup_creates_an_admin(self):
        self.assertEqual(self.res['created_status'], 302)
        self.assertTrue(self.res['row_is_admin'])
        self.assertTrue(self.res['row_hash_is_argon2id'])

    def test_setup_signs_the_new_admin_in(self):
        """Being made to log in with credentials just chosen is pure friction."""
        self.assertEqual(self.res['authed_after_setup'], 200)

    def test_setup_closes_once_an_account_exists(self):
        self.assertEqual(self.res['reget_status'], 302)
        self.assertIn('/login', self.res['reget_location'])
        self.assertEqual(self.res['usurp_status'], 302)
        self.assertFalse(
            self.res['usurper_created'],
            'a second visitor must not be able to claim an account',
        )


class TestSetupValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            def attempt(**form):
                client = app.test_client()
                resp = client.post('/setup', data=form)
                return {
                    'status': resp.status_code,
                    'body': resp.get_data(as_text=True),
                    'created': database.local_account_exists(),
                }

            mismatch = attempt(username='chef', password=GOOD_PASSWORD,
                               confirm_password=GOOD_PASSWORD + 'typo')
            short = attempt(username='chef', password='abc',
                            confirm_password='abc')
            blank_user = attempt(username='   ', password=GOOD_PASSWORD,
                                 confirm_password=GOOD_PASSWORD)
            long_user = attempt(username='x' * 65, password=GOOD_PASSWORD,
                                confirm_password=GOOD_PASSWORD)

            emit(mismatch=mismatch, short=short,
                 blank_user=blank_user, long_user=long_user)
        """)

    def _assert_rejected(self, case, expect_text=None):
        self.assertEqual(case['status'], 400)
        self.assertFalse(case['created'], 'a rejected submission must create nothing')
        if expect_text:
            self.assertIn(expect_text, case['body'])

    def test_mismatched_passwords_rejected(self):
        self._assert_rejected(self.res['mismatch'], 'do not match')

    def test_short_password_rejected(self):
        self._assert_rejected(self.res['short'], 'at least')

    def test_blank_username_rejected(self):
        self._assert_rejected(self.res['blank_user'])

    def test_overlong_username_rejected(self):
        self._assert_rejected(self.res['long_user'])


class TestSetupRaceAndAdoption(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            # Two claims in a row stand in for two concurrent submissions: the
            # guard and the write share one transaction, so the loser gets None.
            first = make_admin('first')
            second = make_admin('second')

            # Fresh database for the adoption case.
            with database.get_db() as conn:
                conn.execute('DELETE FROM users')
                conn.commit()

            # An instance upgrading from AUTH_MODE=none: the account exists with
            # no password, and every job recorded ownership against its name.
            database.ensure_local_user('local')
            needs_setup_with_passwordless_row = database.local_account_exists()

            # The name the setup form offers. It has to be the existing one, or
            # the obvious path through the form orphans that account's history.
            suggested = app.test_client().get('/setup').get_data(as_text=True)

            adopted = make_admin('local')
            row = database.get_user('local')
            with database.get_db() as conn:
                user_count = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]

            emit(
                first_claimed=first is not None,
                second_claimed=second is not None,
                second_row_exists=database.get_user('second') is not None,
                needs_setup_with_passwordless_row=needs_setup_with_passwordless_row,
                setup_page_offers_existing_name='value="local"' in suggested,
                adopted=adopted is not None,
                adopted_has_password=bool(row and row['password_hash']),
                adopted_is_admin=bool(row and row['is_admin']),
                adopted_oidc_sub_is_null=(row or {}).get('oidc_sub') is None,
                user_count=user_count,
            )
        """)

    def test_only_one_claim_can_win(self):
        self.assertTrue(self.res['first_claimed'])
        self.assertFalse(self.res['second_claimed'])
        self.assertFalse(self.res['second_row_exists'])

    def test_a_passwordless_row_does_not_count_as_an_account(self):
        """Otherwise an upgraded instance would show a form nobody can satisfy."""
        self.assertFalse(self.res['needs_setup_with_passwordless_row'])

    def test_setup_adopts_the_passwordless_row(self):
        self.assertTrue(self.res['adopted'])
        self.assertTrue(self.res['adopted_has_password'])
        self.assertTrue(self.res['adopted_is_admin'])
        self.assertTrue(self.res['adopted_oidc_sub_is_null'])

    def test_adoption_reuses_the_row_rather_than_adding_one(self):
        """A new username would orphan the history owned by the old one."""
        self.assertEqual(self.res['user_count'], 1)

    def test_setup_offers_the_adoptable_username(self):
        """Ownership is stored as the username, so adoption hinges on the name.

        The default suggestion is 'admin', which would not match the 'local'
        that AUTH_MODE=none seeded — accepting the form as presented has to keep
        the history rather than silently stranding it.
        """
        self.assertTrue(self.res['setup_page_offers_existing_name'])


class TestLocalLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            make_admin('chef')

            ok_client = app.test_client()
            ok = ok_client.post('/auth/local/login', data={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })
            authed = ok_client.get('/api/jobs').status_code
            me = ok_client.get('/api/me').get_json()

            json_client = app.test_client()
            json_ok = json_client.post('/auth/local/login', json={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })

            bad_client = app.test_client()
            wrong = bad_client.post('/auth/local/login', json={
                'username': 'chef', 'password': 'not the password',
            })
            still_out = bad_client.get('/api/jobs').status_code

            missing = app.test_client().post('/auth/local/login', json={
                'username': 'nobody', 'password': 'not the password',
            })

            # An account with no password must not be reachable with an empty one.
            database.ensure_local_user('sso-user', is_admin=False)
            passwordless = app.test_client().post('/auth/local/login', json={
                'username': 'sso-user', 'password': '',
            })

            emit(
                ok_status=ok.status_code,
                authed=authed,
                me=me,
                json_status=json_ok.status_code,
                json_body=json_ok.get_json(),
                wrong_status=wrong.status_code,
                wrong_error=wrong.get_json()['error'],
                still_out=still_out,
                missing_status=missing.status_code,
                missing_error=missing.get_json()['error'],
                passwordless_status=passwordless.status_code,
            )
        """)

    def test_correct_credentials_sign_in(self):
        self.assertEqual(self.res['ok_status'], 302)
        self.assertEqual(self.res['authed'], 200)
        self.assertEqual(self.res['me']['user'], 'chef')
        self.assertTrue(self.res['me']['is_admin'])
        self.assertEqual(self.res['me']['auth_mode'], 'local')

    def test_json_callers_get_the_identity_back(self):
        self.assertEqual(self.res['json_status'], 200)
        self.assertEqual(self.res['json_body'], {'user': 'chef', 'is_admin': True})

    def test_wrong_password_is_refused(self):
        self.assertEqual(self.res['wrong_status'], 401)
        self.assertEqual(self.res['still_out'], 401)

    def test_unknown_user_and_wrong_password_are_indistinguishable(self):
        """Differing responses would enumerate which accounts exist."""
        self.assertEqual(self.res['wrong_status'], self.res['missing_status'])
        self.assertEqual(self.res['wrong_error'], self.res['missing_error'])
        self.assertEqual(self.res['wrong_error'], 'Invalid username or password.')

    def test_account_without_a_password_cannot_sign_in(self):
        self.assertEqual(self.res['passwordless_status'], 401)


class TestSessionHandling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            make_admin('chef')

            # A planted session value must not survive the privilege change.
            fixation = app.test_client()
            with fixation.session_transaction() as sess:
                sess['planted'] = 'attacker-controlled'
            fixation.post('/auth/local/login', data={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })
            with fixation.session_transaction() as sess:
                planted_survived = 'planted' in sess
                user_after = sess.get('user')

            # A pending share is the reason the user was sent to log in.
            share = app.test_client()
            with share.session_transaction() as sess:
                sess['shared_url'] = 'https://example.com/reel'
            share.post('/auth/local/login', data={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })
            shared_after = share.get('/api/me').get_json()['shared_url']

            out = app.test_client()
            out.post('/auth/local/login', data={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })
            out.get('/logout')
            after_logout = out.get('/api/jobs').status_code
            with out.session_transaction() as sess:
                user_key_remains = 'user' in sess

            emit(
                planted_survived=planted_survived,
                user_after=user_after,
                shared_after=shared_after,
                after_logout=after_logout,
                user_key_remains=user_key_remains,
            )
        """)

    def test_session_is_rotated_on_sign_in(self):
        self.assertFalse(self.res['planted_survived'])
        self.assertEqual(self.res['user_after'], 'chef')

    def test_pending_share_survives_sign_in(self):
        self.assertEqual(self.res['shared_after'], 'https://example.com/reel')

    def test_logout_clears_everything(self):
        self.assertEqual(self.res['after_logout'], 401)
        self.assertFalse(self.res['user_key_remains'])


class TestRehashOnLogin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            from argon2 import PasswordHasher
            weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
                GOOD_PASSWORD
            )
            database.claim_first_local_admin('chef', weak)

            app.test_client().post('/auth/local/login', data={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })
            stored = database.get_user('chef')['password_hash']

            emit(
                was_outdated=passwords.needs_rehash(weak),
                changed=stored != weak,
                now_current=not passwords.needs_rehash(stored),
                still_verifies=passwords.verify(stored, GOOD_PASSWORD),
            )
        """)

    def test_outdated_hash_is_upgraded_on_login(self):
        """The password is in hand only during login; it is the one chance."""
        self.assertTrue(self.res['was_outdated'])
        self.assertTrue(self.res['changed'])
        self.assertTrue(self.res['now_current'])

    def test_upgraded_hash_still_accepts_the_password(self):
        self.assertTrue(self.res['still_verifies'])


class TestLoginThrottling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            make_admin('chef')

            def wrong_attempts(username, n):
                client = app.test_client()
                return [
                    client.post('/auth/local/login', json={
                        'username': username, 'password': 'wrong',
                    }).status_code
                    for _ in range(n)
                ]

            statuses = wrong_attempts('chef', 6)
            throttled = app.test_client().post('/auth/local/login', json={
                'username': 'chef', 'password': 'wrong',
            })

            # Under the free-attempt threshold, the right password still works.
            login_throttle.clear_all()
            wrong_attempts('chef', 3)
            recovers = app.test_client().post('/auth/local/login', json={
                'username': 'chef', 'password': GOOD_PASSWORD,
            }).status_code

            # A success clears the counter, so an earlier typo leaves no penalty.
            login_throttle.clear_all()
            wrong_attempts('chef', 3)
            app.test_client().post('/auth/local/login', json={
                'username': 'chef', 'password': GOOD_PASSWORD,
            })
            after_success = app.test_client().post('/auth/local/login', json={
                'username': 'chef', 'password': GOOD_PASSWORD,
            }).status_code

            # Hammering one name must not lock a different one out.
            login_throttle.clear_all()
            wrong_attempts('someone-else', 8)
            other_unaffected = app.test_client().post('/auth/local/login', json={
                'username': 'chef', 'password': GOOD_PASSWORD,
            }).status_code

            emit(
                statuses=statuses,
                throttled_status=throttled.status_code,
                retry_after=throttled.headers.get('Retry-After'),
                recovers=recovers,
                after_success=after_success,
                other_unaffected=other_unaffected,
            )
        """)

    def test_repeated_failures_start_being_throttled(self):
        self.assertIn(429, self.res['statuses'],
                      f"never throttled: {self.res['statuses']}")

    def test_first_attempts_are_not_throttled(self):
        """A mistyped password should not be met with a delay straight away."""
        self.assertEqual(self.res['statuses'][:3], [401, 401, 401])

    def test_throttled_response_says_when_to_retry(self):
        self.assertEqual(self.res['throttled_status'], 429)
        self.assertIsNotNone(self.res['retry_after'])

    def test_correct_password_works_below_the_threshold(self):
        self.assertEqual(self.res['recovers'], 200)

    def test_a_successful_sign_in_clears_the_counter(self):
        self.assertEqual(self.res['after_success'], 200)

    def test_throttling_one_account_does_not_lock_out_another(self):
        """Otherwise brute-force defence becomes a denial-of-service tool."""
        self.assertEqual(self.res['other_unaffected'], 200)


class TestAuthStatus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = _run_local("""
            client = app.test_client()
            before = client.get('/api/auth/status')
            make_admin('secret-admin')
            after = client.get('/api/auth/status')

            emit(
                before_status=before.status_code,
                before=before.get_json(),
                after=after.get_json(),
                after_body=after.get_data(as_text=True),
            )
        """)

    def test_status_is_public(self):
        """The client needs it before it can possibly be authenticated."""
        self.assertEqual(self.res['before_status'], 200)

    def test_setup_required_flips_once_an_account_exists(self):
        self.assertTrue(self.res['before']['setup_required'])
        self.assertFalse(self.res['after']['setup_required'])

    def test_status_never_leaks_a_username(self):
        self.assertNotIn('secret-admin', self.res['after_body'])


if __name__ == '__main__':
    unittest.main()
