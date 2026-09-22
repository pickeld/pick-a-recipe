"""Web Push notifications for recipe outcomes.

Covers the three things that would fail silently in production: keys that are
not stable (every subscription dies on restart), dead endpoints that are never
pruned (a failed request per device per recipe, forever), and the two auth
holes in the pre-existing endpoints.
"""

import base64
import os
import sys
import tempfile
import textwrap
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

os.environ['DATA_DIR'] = tempfile.mkdtemp()

from app_harness import result as _result, run as _run  # noqa: E402


class _ImmediateExecutor:
    """Runs submitted work inline so tests do not race the thread pool."""

    def __init__(self):
        self.submitted = 0

    def submit(self, fn, *args, **kwargs):
        self.submitted += 1
        fn(*args, **kwargs)


def _clear_subscriptions():
    from database import get_db
    with get_db() as conn:
        conn.execute('DELETE FROM push_subscriptions')
        conn.commit()


class TestVapidKeys(unittest.TestCase):
    """The key pair has to survive restarts and be shaped the way browsers want."""

    def setUp(self):
        import config as config_module
        import push
        self.push = push
        self.config_module = config_module
        for var in ('VAPID_PUBLIC_KEY', 'VAPID_PRIVATE_KEY'):
            os.environ.pop(var, None)

    def test_generated_public_key_is_an_uncompressed_p256_point(self):
        public, private = self.push.ensure_keys()
        self.assertTrue(public and private)

        raw = base64.urlsafe_b64decode(public + '=' * (-len(public) % 4))
        # PushManager rejects anything else, and it does so with an opaque
        # InvalidAccessError that gives no hint about the encoding.
        self.assertEqual(len(raw), 65)
        self.assertEqual(raw[0], 0x04)

    def test_keys_are_stable_across_calls(self):
        # A second pair would invalidate every existing subscription without
        # any visible error: the sends just stop being delivered.
        self.assertEqual(self.push.ensure_keys(), self.push.ensure_keys())

    def test_persisted_key_survives_a_config_reload(self):
        public, _ = self.push.ensure_keys()
        self.config_module.config.reload()
        self.assertEqual(self.push.ensure_keys()[0], public)

    def test_environment_overrides_the_stored_pair(self):
        os.environ['VAPID_PUBLIC_KEY'] = 'env-public'
        os.environ['VAPID_PRIVATE_KEY'] = 'env-private'
        try:
            self.assertEqual(self.push.ensure_keys(), ('env-public', 'env-private'))
        finally:
            del os.environ['VAPID_PUBLIC_KEY']
            del os.environ['VAPID_PRIVATE_KEY']

    def test_private_key_is_not_hardcoded_anywhere(self):
        # The pair is a credential. It must come from the environment or be
        # generated at runtime, never from a literal in the source.
        import inspect
        source = inspect.getsource(self.push) + inspect.getsource(self.config_module)
        _, private = self.push.ensure_keys()
        self.assertNotIn(private, source)


class TestSubscriptionOwnership(unittest.TestCase):
    """Endpoints arrive from the client, so deletes must be owner-scoped."""

    def setUp(self):
        _clear_subscriptions()

    def test_scoped_delete_refuses_another_users_endpoint(self):
        from database import (
            delete_push_subscription, get_push_subscriptions,
            save_push_subscription,
        )
        save_push_subscription('alice', 'https://push.example/alice', 'p', 'a')

        removed = delete_push_subscription(
            'https://push.example/alice', username='bob',
        )

        self.assertFalse(removed)
        self.assertEqual(len(get_push_subscriptions('alice')), 1)

    def test_scoped_delete_removes_your_own_endpoint(self):
        from database import (
            delete_push_subscription, get_push_subscriptions,
            save_push_subscription,
        )
        save_push_subscription('alice', 'https://push.example/alice', 'p', 'a')

        removed = delete_push_subscription(
            'https://push.example/alice', username='alice',
        )

        self.assertTrue(removed)
        self.assertEqual(get_push_subscriptions('alice'), [])


class TestDelivery(unittest.TestCase):
    """Fan-out, pruning, and the guarantee that a push never breaks a recipe."""

    def setUp(self):
        import push
        from database import save_push_subscription
        self.push = push
        _clear_subscriptions()
        save_push_subscription('alice', 'https://push.example/one', 'p1', 'a1')
        save_push_subscription('alice', 'https://push.example/two', 'p2', 'a2')

        self._real_webpush = push.webpush
        self._real_executor = push._executor
        push._executor = _ImmediateExecutor()

    def tearDown(self):
        self.push.webpush = self._real_webpush
        self.push._executor = self._real_executor

    def _capture(self, side_effect=None):
        calls = []

        def fake_webpush(**kwargs):
            calls.append(kwargs)
            if side_effect:
                side_effect(kwargs)

        self.push.webpush = fake_webpush
        return calls

    def test_notifies_every_device_the_user_registered(self):
        calls = self._capture()

        self.push.notify_user(
            'alice', title='Recipe saved', body='Shakshuka', url='/jobs/abc',
        )

        self.assertEqual(len(calls), 2)
        endpoints = {c['subscription_info']['endpoint'] for c in calls}
        self.assertEqual(
            endpoints, {'https://push.example/one', 'https://push.example/two'},
        )

    def test_payload_carries_title_body_and_deep_link(self):
        import json
        calls = self._capture()

        self.push.notify_user(
            'alice', title='Recipe saved', body='Shakshuka', url='/jobs/abc',
            tag='job-abc',
        )

        payload = json.loads(calls[0]['data'])
        self.assertEqual(payload['title'], 'Recipe saved')
        self.assertEqual(payload['body'], 'Shakshuka')
        self.assertEqual(payload['url'], '/jobs/abc')
        self.assertEqual(payload['tag'], 'job-abc')

    def test_gone_endpoint_is_pruned(self):
        from database import get_push_subscriptions

        def reject_first(kwargs):
            if kwargs['subscription_info']['endpoint'].endswith('/one'):
                exc = self.push.WebPushException('gone')
                exc.response = SimpleNamespace(status_code=410)
                raise exc

        self._capture(side_effect=reject_first)
        self.push.notify_user('alice', title='t', body='b')

        remaining = {s['endpoint'] for s in get_push_subscriptions('alice')}
        self.assertEqual(remaining, {'https://push.example/two'})

    def test_transient_failure_keeps_the_subscription(self):
        from database import get_push_subscriptions

        def server_error(_kwargs):
            exc = self.push.WebPushException('boom')
            exc.response = SimpleNamespace(status_code=503)
            raise exc

        self._capture(side_effect=server_error)
        self.push.notify_user('alice', title='t', body='b')

        # A push service having a bad day must not cost the user their devices.
        self.assertEqual(len(get_push_subscriptions('alice')), 2)

    def test_delivery_failure_never_propagates(self):
        def explode(_kwargs):
            raise RuntimeError('network on fire')

        self._capture(side_effect=explode)

        # Called from the worker thread mid-completion: raising here would
        # fail a recipe that actually succeeded.
        self.push.notify_user('alice', title='t', body='b')

    def test_anonymous_job_sends_nothing(self):
        calls = self._capture()
        self.push.notify_user(None, title='t', body='b')
        self.assertEqual(calls, [])

    def test_offline_devices_still_get_the_notification(self):
        calls = self._capture()

        self.push.notify_user('alice', title='t', body='b')

        # pywebpush defaults ttl to 0, which tells the push service to discard
        # anything it cannot deliver this instant -- i.e. exactly the case this
        # feature exists for, since extraction finishes after the user leaves.
        self.assertGreater(calls[0]['ttl'], 0)

    def test_long_bodies_are_truncated(self):
        import json
        calls = self._capture()

        self.push.notify_user('alice', title='t', body='x' * 5000)

        body = json.loads(calls[0]['data'])['body']
        self.assertLessEqual(len(body), self.push._MAX_BODY_CHARS)
        self.assertTrue(body.endswith('…'))


class TestJobLifecycleHooks(unittest.TestCase):
    """Each user-visible outcome has to reach the owner."""

    def setUp(self):
        import job_manager
        self.job_manager = job_manager
        self.sent = []

        def record(username, **kwargs):
            self.sent.append((username, kwargs))

        self._real_notify = job_manager.notify_user
        job_manager.notify_user = record

        class _Socket:
            def emit(self, *args, **kwargs):
                pass

        self.jm = job_manager.JobManager(_Socket())

    def tearDown(self):
        self.job_manager.notify_user = self._real_notify

    def _job(self, user_id='alice'):
        from database import create_job
        return create_job('https://example.com/v', user_id=user_id)

    def test_completion_names_the_recipe_and_its_destination(self):
        job_id = self._job()

        self.jm.complete_job(job_id, {'name': 'Shakshuka'}, None, 'mealie')

        username, kwargs = self.sent[-1]
        self.assertEqual(username, 'alice')
        self.assertEqual(kwargs['title'], 'Recipe saved')
        self.assertIn('Shakshuka', kwargs['body'])
        self.assertIn('mealie', kwargs['body'])
        self.assertEqual(kwargs['url'], f'/jobs/{job_id}')

    def test_failure_carries_the_error(self):
        job_id = self._job()

        self.jm.fail_job(job_id, 'Transcription timed out')

        username, kwargs = self.sent[-1]
        self.assertEqual(username, 'alice')
        self.assertEqual(kwargs['title'], 'Recipe failed')
        self.assertIn('Transcription timed out', kwargs['body'])

    def test_approval_warns_about_the_deadline(self):
        job_id = self._job()
        self.jm.transition(job_id, self.job_manager.RUNNING)

        self.jm.open_approval(
            job_id, {'name': 'Shakshuka'}, None, [], 'mealie', timeout_minutes=7,
        )

        username, kwargs = self.sent[-1]
        self.assertEqual(username, 'alice')
        self.assertIn('approval', kwargs['title'].lower())
        # Missing an approval loses the extraction, so the window has to be
        # in the notification itself.
        self.assertIn('7', kwargs['body'])
        # And it must not outlive that window: arriving late, it would ask for
        # a decision the server has already given up on.
        self.assertEqual(kwargs['ttl'], 7 * 60)

    def test_all_three_collapse_onto_one_notification_per_job(self):
        job_id = self._job()
        self.jm.transition(job_id, self.job_manager.RUNNING)
        self.jm.open_approval(job_id, {'name': 'S'}, None, [], 'mealie')
        self.jm.complete_job(job_id, {'name': 'S'}, None, 'mealie')

        tags = {kwargs['tag'] for _, kwargs in self.sent}
        self.assertEqual(tags, {f'job-{job_id}'})


_PRELUDE = """
    import database, mobile_auth
    from app import app

    app.config['TESTING'] = True

    def bearer(username):
        # The account has to exist: _bearer_identity re-reads it from the
        # database rather than trusting the token's claims.
        database.upsert_oidc_user(
            sub='sub-' + username, username=username, is_admin=False,
        )
        pair = mobile_auth.issue_token_pair(username, is_admin=False)
        return {'Authorization': 'Bearer ' + pair['access_token']}

    SUB = {
        'endpoint': 'https://push.example/alice',
        'keys': {'p256dh': 'p', 'auth': 'a'},
    }
"""


def _run_app(script: str, **env) -> dict:
    env.setdefault('JWT_SECRET_KEY', 'test-only-signing-key-of-adequate-length-0123456789')
    return _result(_run(textwrap.dedent(_PRELUDE) + textwrap.dedent(script), **env))


class TestPushEndpoints(unittest.TestCase):
    """HTTP surface, in a forked interpreter so the Flask app imports cleanly."""

    def test_subscribe_accepts_a_bearer_identity(self):
        # Regression: the handler read session['user'] directly, so every
        # subscribe from the Android app raised KeyError -> 500.
        out = _run_app("""
            client = app.test_client()
            resp = client.post('/api/push/subscribe',
                               json={'subscription': SUB},
                               headers=bearer('alice'))
            owners = [s['endpoint'] for s in database.get_push_subscriptions('alice')]
            emit(status=resp.status_code, owners=owners)
        """)
        self.assertEqual(out['status'], 200)
        self.assertEqual(out['owners'], ['https://push.example/alice'])

    def test_unsubscribe_cannot_silence_another_users_device(self):
        out = _run_app("""
            client = app.test_client()
            client.post('/api/push/subscribe', json={'subscription': SUB},
                        headers=bearer('alice'))

            resp = client.post('/api/push/unsubscribe',
                               json={'endpoint': SUB['endpoint']},
                               headers=bearer('mallory'))

            emit(status=resp.status_code,
                 alice_still_subscribed=len(database.get_push_subscriptions('alice')))
        """)
        self.assertEqual(out['alice_still_subscribed'], 1)

    def test_vapid_key_endpoint_serves_a_subscribable_key(self):
        out = _run_app("""
            resp = app.test_client().get('/api/push/vapid-key',
                                         headers=bearer('alice'))
            emit(status=resp.status_code, key=resp.get_json()['key'])
        """)
        self.assertEqual(out['status'], 200)
        raw = base64.urlsafe_b64decode(out['key'] + '=' * (-len(out['key']) % 4))
        self.assertEqual(len(raw), 65)

    def test_push_endpoints_require_authentication(self):
        out = _run_app("""
            client = app.test_client()
            emit(subscribe=client.post('/api/push/subscribe',
                                       json={'subscription': SUB}).status_code,
                 vapid=client.get('/api/push/vapid-key').status_code)
        """)
        self.assertNotEqual(out['subscribe'], 200)
        self.assertNotEqual(out['vapid'], 200)


if __name__ == '__main__':
    unittest.main()
