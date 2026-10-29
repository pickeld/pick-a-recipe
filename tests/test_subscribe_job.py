"""subscribe_job must accept the payload the current UI actually sends.

The SPA emits the job id as a string. Older pages emit {"job_id": "..."}.
Calling .get on the string raised AttributeError and dropped the subscription
(issue #65).
"""

import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_harness import result as _result, run as _run  # noqa: E402


class SubscribeJobPayloadTests(unittest.TestCase):
    def test_string_and_object_payloads_both_subscribe(self):
        script = textwrap.dedent("""
            from app import app, socketio
            from database import create_job, create_local_user

            create_local_user('tester', 'hash', is_admin=True)
            job_id = create_job('https://example.com/watch', user_id='tester')

            client = app.test_client()
            with client.session_transaction() as session:
                session['user'] = 'tester'
                session['is_admin'] = True
            sio = socketio.test_client(app, flask_test_client=client)
            sio.get_received()

            sio.emit('subscribe_job', job_id)
            as_string = sio.get_received()

            sio.emit('unsubscribe_job', job_id)
            unsubscribed = sio.get_received()

            sio.emit('subscribe_job', {'job_id': job_id})
            as_object = sio.get_received()

            sio.emit('subscribe_job', 12)
            ignored = sio.get_received()

            def names(events):
                return [event['name'] for event in events]

            emit(
                connected=sio.is_connected(),
                job_id=job_id,
                as_string=names(as_string),
                string_job=(as_string[0].get('args') or [{}])[0].get('job_id') if as_string else None,
                unsubscribed=names(unsubscribed),
                as_object=names(as_object),
                ignored=names(ignored),
            )
        """)
        out = _result(_run(script))
        self.assertTrue(out['connected'], out)
        self.assertEqual(out['as_string'], ['subscribed'], out)
        self.assertEqual(out['string_job'], out['job_id'], out)
        self.assertEqual(out['unsubscribed'], ['unsubscribed'], out)
        self.assertEqual(out['as_object'], ['subscribed'], out)
        self.assertEqual(out['ignored'], [], out)

    def test_upload_handlers_ignore_non_object_payloads(self):
        script = textwrap.dedent("""
            from app import app, socketio
            from database import create_local_user

            create_local_user('tester', 'hash', is_admin=True)

            client = app.test_client()
            with client.session_transaction() as session:
                session['user'] = 'tester'
                session['is_admin'] = True
            sio = socketio.test_client(app, flask_test_client=client)
            sio.get_received()

            for event in ('confirm_upload', 'cancel_upload'):
                sio.emit(event, 'some-upload-id')
                sio.emit(event, 12)

            emit(
                connected=sio.is_connected(),
                received=[event['name'] for event in sio.get_received()],
            )
        """)
        out = _result(_run(script))
        self.assertTrue(out['connected'], out)
        self.assertEqual(out['received'], [], out)
