"""Redesign tests: state machine, CAS claims, slot-free approvals, scoping.

Covers plan items W1.T2/W1.T4 behavior contracts. Conventions follow
tests/test_job_queue_auth.py (stdlib unittest + DATA_DIR tempdir).
"""

import os
import sys
import tempfile
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir

from database import (
    get_db, get_job, get_pending_upload, init_db,
)
init_db()

from job_manager import (
    JobManager, InvalidTransition, AWAITING_APPROVAL, CANCELLED,
    QUEUED, RUNNING,
)


class FakeSocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, room=None):
        self.events.append((event, payload, room))


def _purge_jobs(job_ids):
    with get_db() as conn:
        conn.executemany(
            'DELETE FROM recipe_jobs WHERE id = ?', [(j,) for j in job_ids]
        )
        conn.commit()


def _purge_uploads(upload_id):
    with get_db() as conn:
        conn.execute('DELETE FROM pending_uploads WHERE id = ?', (upload_id,))
        conn.commit()


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class StateMachineTestCase(unittest.TestCase):
    def setUp(self):
        self.jm = JobManager(FakeSocketIO())

    def test_invalid_transition_raises(self):
        jid = self.jm.create_new_job('https://example.com/t1', user_id='alice')
        try:
            with self.assertRaises(InvalidTransition):
                self.jm.transition(jid, 'completed')
        finally:
            _purge_jobs([jid])

    def test_valid_transition_chain(self):
        jid = self.jm.create_new_job('https://example.com/t2', user_id='alice')
        try:
            self.assertTrue(self.jm.transition(
                jid, RUNNING, expected_old=QUEUED))
            self.assertTrue(self.jm.transition(
                jid, AWAITING_APPROVAL, percent=90))
            job = get_job(jid)
            self.assertEqual(job['status'], AWAITING_APPROVAL)
            self.assertEqual(job['progress'], 90)
            self.assertIsNotNone(job['state_changed_at'])
        finally:
            _purge_jobs([jid])

    def test_open_approval_from_queued_is_rejected_gracefully(self):
        jid = self.jm.create_new_job('https://example.com/t5', user_id='alice')
        try:
            with self.assertRaises(InvalidTransition):
                self.jm.transition(
                    jid, AWAITING_APPROVAL, percent=90,
                    stage_message='Waiting for your confirmation...')
        finally:
            _purge_jobs([jid])

    def test_cas_claim_single_winner(self):
        jid = self.jm.create_new_job('https://example.com/t3', user_id='alice')
        try:
            first = self.jm.transition(jid, RUNNING, expected_old=QUEUED)
            second = self.jm.transition(jid, RUNNING, expected_old=QUEUED)
            self.assertTrue(first)
            self.assertFalse(second)
        finally:
            _purge_jobs([jid])

    def test_terminal_states_are_immutable(self):
        jid = self.jm.create_new_job('https://example.com/t4', user_id='alice')
        try:
            self.jm.cancel_job(jid)
            with self.assertRaises(InvalidTransition):
                self.jm.transition(jid, RUNNING)
        finally:
            _purge_jobs([jid])


class SlotFreeApprovalTestCase(unittest.TestCase):
    """The core redesign contract: a parked approval must not occupy the
    only worker slot."""

    def setUp(self):
        os.environ['MAX_CONCURRENT_JOBS'] = '1'
        self.jm = JobManager(FakeSocketIO())
        self.jm.set_process_func(lambda job_id, jm: None)

    def tearDown(self):
        os.environ.pop('MAX_CONCURRENT_JOBS', None)

    def _park(self, url, user):
        jid = self.jm.create_new_job(url, user_id=user)
        self.jm.transition(jid, RUNNING, expected_old=QUEUED)
        upload_id = self.jm.open_approval(
            jid, {'name': f'recipe-{jid[:6]}'}, None, [], 'mealie')
        return jid, upload_id

    def test_preview_progress_keeps_the_approval(self):
        """The preview progress line must not consume the approval transition.

        Doing that deleted the pending upload, left the job as "Needs
        approval" with no Approve button, and the sweeper then expired it.
        """
        jid = self.jm.create_new_job('https://example.com/preview', user_id='alice')
        upload_id = None
        try:
            self.jm.transition(jid, RUNNING, expected_old=QUEUED)
            self.jm.update_progress(
                jid, 'preview', 'Waiting for your confirmation...', 90)
            self.assertEqual(get_job(jid)['status'], RUNNING)
            with self.assertLogs('jobs', level='INFO') as captured:
                upload_id = self.jm.open_approval(
                    jid, {'name': 'Pasta al forno'}, None, [], 'Mealie')
            self.assertTrue(upload_id)
            self.assertEqual(get_pending_upload(upload_id)['status'], 'pending')
            self.assertEqual(get_job(jid)['status'], AWAITING_APPROVAL)
            text = '\n'.join(captured.output)
            self.assertIn('Nothing has been sent to Mealie yet', text)
            self.assertIn('Pasta al forno', text)
        finally:
            _purge_jobs([jid])
            if upload_id:
                _purge_uploads(upload_id)

    def test_parked_approval_frees_slot_for_next_job(self):
        parked, upload_id = self._park('https://example.com/park', 'alice')
        follower = self.jm.create_new_job('https://example.com/follow',
                                          user_id='bob')

        done = threading.Event()
        observed_statuses = {}

        def process(job_id, jm):
            observed_statuses[job_id] = get_job(job_id)['status']
            done.set()

        try:
            self.assertEqual(get_job(parked)['status'], AWAITING_APPROVAL)
            self.jm.start_job(follower, process)

            # The single worker must reach the *follower* even though the
            # first job sits in awaiting_approval forever.
            self.assertTrue(done.wait(timeout=5),
                            'worker never picked up second job; '
                            'approval is blocking a slot')
            self.assertEqual(observed_statuses.get(follower), RUNNING)
        finally:
            _purge_jobs([parked, follower])
            _purge_uploads(upload_id)

    def test_confirm_schedules_resume_and_reject_cancels(self):
        resumed = []

        class ResumeRecorder:
            def __call__(self, job_id, jm):
                resumed.append(job_id)

        parked_a, up_a = self._park('https://example.com/ra', 'alice')
        parked_b, up_b = self._park('https://example.com/rb', 'bob')
        self.jm.set_resume_func(ResumeRecorder())
        try:
            result = self.jm.confirm_approval(up_a, selected_image_index=0)
            self.assertTrue(result['ok'])
            self.assertTrue(
                _wait_until(lambda: parked_a in resumed),
                'resume worker never ran for approved job')
            self.assertEqual(get_job(parked_a)['status'], 'uploading')

            rejected = self.jm.reject_approval(up_b)
            self.assertTrue(rejected['ok'])
            self.assertEqual(get_job(parked_b)['status'], CANCELLED)
            self.assertEqual(get_pending_upload(up_b)['status'], 'cancelled')

            # One-shot guards on both paths.
            self.assertFalse(self.jm.confirm_approval(up_b)['ok'])
            self.assertFalse(self.jm.reject_approval(up_a)['ok'])
        finally:
            _purge_jobs([parked_a, parked_b])
            _purge_uploads(up_a)
            _purge_uploads(up_b)

    def test_cancel_parked_job_clears_its_approval(self):
        from job_manager import cancel_pending_upload_for_job
        parked, upload_id = self._park('https://example.com/cc', 'alice')
        try:
            self.assertTrue(self.jm.cancel_job(parked))
            self.assertEqual(get_job(parked)['status'], CANCELLED)
            row = get_pending_upload(upload_id)
            self.assertEqual(row['status'], 'cancelled')
        finally:
            _purge_jobs([parked])
            _purge_uploads(upload_id)


class ScopingTestCase(unittest.TestCase):
    def setUp(self):
        self.jm = JobManager(FakeSocketIO())

    def test_owner_reads_and_admin_bypass(self):
        alice_job = self.jm.create_new_job('https://example.com/sa',
                                           user_id='alice')
        bob_job = self.jm.create_new_job('https://example.com/sb',
                                         user_id='bob')
        try:
            self.assertIsNone(get_job(alice_job, user_id='bob'))
            self.assertIsNotNone(get_job(alice_job, user_id='alice'))
            self.assertIsNotNone(get_job(alice_job, is_admin=True))

            bob_view = {j['id'] for j in
                        self.jm.get_all_active_jobs(user_id='bob')}
            self.assertNotIn(alice_job, bob_view)
            admin_view = {j['id'] for j in
                          self.jm.get_all_active_jobs(is_admin=True)}
            self.assertLessEqual({alice_job, bob_job}, admin_view)
        finally:
            _purge_jobs([alice_job, bob_job])

    def test_approvals_scoped_to_owner(self):
        jid = self.jm.create_new_job('https://example.com/ap', user_id='alice')
        self.jm.transition(jid, RUNNING, expected_old=QUEUED)
        upload_id = self.jm.open_approval(
            jid, {'name': 'scoped'}, None, [], 'mealie')
        try:
            alice_view = {u['id'] for u in
                          self.jm.get_approvals(user_id='alice')}
            bob_view = {u['id'] for u in self.jm.get_approvals(user_id='bob')}
            admin_view = {u['id'] for u in self.jm.get_approvals(is_admin=True)}
            self.assertIn(upload_id, alice_view)
            self.assertNotIn(upload_id, bob_view)
            self.assertIn(upload_id, admin_view)
        finally:
            _purge_jobs([jid])
            _purge_uploads(upload_id)


class RestartRecoveryTestCase(unittest.TestCase):
    def test_restart_preserves_awaiting_approval_and_requeues_queued(self):
        jm1 = JobManager(FakeSocketIO())
        parked = jm1.create_new_job('https://example.com/rr1', user_id='alice')
        jm1.transition(parked, RUNNING, expected_old=QUEUED)
        upload_id = jm1.open_approval(parked, {'name': 'survivor'},
                                      None, [], 'mealie')
        queued = jm1.create_new_job('https://example.com/rr2', user_id='alice')

        # Simulate a restart against the same DATA_DIR database.
        jm2 = JobManager(FakeSocketIO())
        jm2.set_process_func(lambda job_id, jm: None)

        deadline = time.time() + 5
        while time.time() < deadline and \
                get_job(queued)['status'] not in (RUNNING,):
            time.sleep(0.05)

        try:
            self.assertEqual(get_job(parked)['status'], AWAITING_APPROVAL,
                             'restart must not kill approval-parked jobs')
            self.assertEqual(get_job(queued)['status'], RUNNING)
        finally:
            _purge_jobs([parked, queued])
            _purge_uploads(upload_id)


if __name__ == '__main__':
    unittest.main()
