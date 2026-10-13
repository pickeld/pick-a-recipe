"""Wave-3 resilience tests: leases, heartbeat helper, sweeper, artifact pruning."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir


def _sql(query, params=()):
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        conn.commit()
        return cursor


class FakeSocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload=None, room=None):
        self.events.append((event, payload, room))


class TestWalMode(unittest.TestCase):
    def test_journal_mode_is_wal(self):
        from database import init_db, get_db
        init_db()
        with get_db() as conn:
            mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
        self.assertEqual(str(mode).lower(), 'wal')


class TestLeaseClaim(unittest.TestCase):
    def setUp(self):
        from job_manager import JobManager
        self.jm = JobManager(FakeSocketIO())

    def test_claim_writes_future_lease(self):
        from database import get_db, get_job
        jid = self.jm.create_new_job('https://example.com/wl', user_id='a')
        try:
            ok = self.jm.transition(
                jid, 'running', expected_old='queued',
                claim_only_if_ready=True, with_lease_minutes=self.jm.LEASE_MINUTES,
            )
            self.assertTrue(ok)
            job = get_job(jid)
            self.assertIsNotNone(job['lease_expires_at'])
        finally:
            _sql('DELETE FROM recipe_jobs WHERE id = ?', (jid,))

    def test_claim_blocked_until_next_run_at(self):
        from database import get_db, get_job
        jid = self.jm.create_new_job('https://example.com/nr', user_id='a')
        try:
            _sql("UPDATE recipe_jobs SET next_run_at = datetime('now','+120 seconds')"
                 ' WHERE id = ?', (jid,))
            blocked = self.jm.transition(
                jid, 'running', expected_old='queued',
                claim_only_if_ready=True, with_lease_minutes=10,
            )
            self.assertFalse(blocked,
                             'backed-off job must not be claimable')

            _sql('UPDATE recipe_jobs SET next_run_at = NULL WHERE id = ?', (jid,))
            ready = self.jm.transition(
                jid, 'running', expected_old='queued',
                claim_only_if_ready=True, with_lease_minutes=10,
            )
            self.assertTrue(ready)
        finally:
            _purge(jid)


class TestSweepStaleLeases(unittest.TestCase):
    def setUp(self):
        from job_manager import JobManager
        self.jm = JobManager(FakeSocketIO())

    def _stale_running(self, url, attempts):
        from database import create_job
        jid = create_job(url, user_id='alice')
        _sql('''
            UPDATE recipe_jobs SET status = 'running',
                lease_expires_at = datetime('now', '-5 minutes'),
                attempts = ?
            WHERE id = ?
        ''', (attempts, jid))
        return jid

    def test_stale_lease_requeues_with_backoff(self):
        jid = self._stale_running('https://example.com/sl1', 0)
        try:
            result = self.jm.sweep_once()
            self.assertGreaterEqual(result['requeued'], 1)

            from database import get_job
            job = get_job(jid)
            self.assertEqual(job['status'], 'queued')
            self.assertEqual(job['attempts'], 1)
            self.assertIsNotNone(job['next_run_at'])

            # Backoff must block an immediate re-claim.
            blocked = self.jm.transition(
                jid, 'running', expected_old='queued',
                claim_only_if_ready=True,
            )
            self.assertFalse(blocked)
        finally:
            _purge(jid)

    def test_repeated_loser_fails_for_good(self):
        jid = self._stale_running('https://example.com/sl2', 3)
        try:
            self.jm.sweep_once()
            from database import get_job
            job = get_job(jid)
            self.assertEqual(job['status'], 'failed')
            self.assertIn('repeatedly', job['error_message'])
        finally:
            _purge(jid)


class TestHeartbeatExtend(unittest.TestCase):
    def test_extends_only_live_workers(self):
        from database import (
            create_job, extend_leases, get_db, get_job,
        )
        running = create_job('https://example.com/hb1', user_id='a')
        queued = create_job('https://example.com/hb2', user_id='a')
        try:
            for jid in (running, queued):
                _sql('''
                    UPDATE recipe_jobs
                    SET lease_expires_at = datetime('now', '-5 minutes')
                    WHERE id = ?
                ''', (jid,))
            _sql("UPDATE recipe_jobs SET status='running' WHERE id=?",
                 (running,))

            extended = extend_leases([running, queued], minutes=10)
            self.assertEqual(extended, 1)

            self.assertGreater(
                str(get_job(running)['lease_expires_at']),
                str(get_job(queued)['lease_expires_at']))
        finally:
            _purge([running, queued])


class TestApprovalExpirySweep(unittest.TestCase):
    def test_a_past_deadline_does_not_expire_the_approval(self):
        from database import (
            create_pending_upload, expire_due_approvals, get_db,
            get_pending_upload,
        )
        from job_manager import (
            AWAITING_APPROVAL, JobManager, RUNNING,
        )
        jm = JobManager(FakeSocketIO())
        jid = jm.create_new_job('https://example.com/ex', user_id='alice')
        up = 'up_' + os.urandom(8).hex()
        create_pending_upload(
            upload_id=up, job_id=jid, recipe_data={'name': 'Exp'},
            image_path=None, image_candidates=[], output_target='mealie',
            best_image_index=0, timeout_minutes=5, user_id='alice',
        )
        _sql("UPDATE pending_uploads SET expires_at = datetime('now','-1 minute')"
             ' WHERE id = ?', (up,))
        try:
            jm.transition(jid, RUNNING, expected_old='queued')
            jm.transition(jid, AWAITING_APPROVAL, percent=90)

            expired_jobs = expire_due_approvals()
            self.assertEqual(expired_jobs, [])

            result = jm.sweep_once()
            self.assertEqual(result.get('expired', 0), 0)

            self.assertEqual(get_pending_upload(up)['status'], 'pending')
            from database import get_job
            self.assertEqual(get_job(jid)['status'], AWAITING_APPROVAL)
        finally:
            _purge(jid)
            _sql('DELETE FROM pending_uploads WHERE id = ?', (up,))

    def test_confirmed_upload_is_not_expired_while_it_waits_to_upload(self):
        """Approving must not look like an abandoned approval.

        The sweeper used to treat every non-pending row as stranded, so a
        confirmed recipe was marked expired before Mealie ever saw it.
        """
        from database import (
            confirm_pending_upload, create_pending_upload, get_job,
            get_pending_upload,
        )
        from job_manager import AWAITING_APPROVAL, JobManager, RUNNING

        jm = JobManager(FakeSocketIO())
        jid = jm.create_new_job('https://example.com/confirmed', user_id='alice')
        up = 'up_' + os.urandom(8).hex()
        create_pending_upload(
            upload_id=up, job_id=jid, recipe_data={'name': 'Kept'},
            image_path=None, image_candidates=[], output_target='mealie',
            best_image_index=0, timeout_minutes=5, user_id='alice',
        )
        try:
            jm.transition(jid, RUNNING, expected_old='queued')
            jm.transition(jid, AWAITING_APPROVAL, percent=90)
            self.assertTrue(confirm_pending_upload(up, 0))

            jm.sweep_once()

            self.assertEqual(get_pending_upload(up)['status'], 'confirmed')
            self.assertEqual(get_job(jid)['status'], AWAITING_APPROVAL)
        finally:
            _purge(jid)
            _sql('DELETE FROM pending_uploads WHERE id = ?', (up,))


class TestPruneArtifactDirs(unittest.TestCase):
    def test_orphans_pruned_live_kept(self):
        from database import create_job
        from job_manager import prune_artifact_dirs
        from database import DATA_DIR

        live = create_job('https://example.com/art', user_id='alice')
        orphan = 'orphan-' + os.urandom(8).hex()
        artifacts_root = os.path.join(DATA_DIR, 'artifacts')
        os.makedirs(os.path.join(artifacts_root, live), exist_ok=True)
        os.makedirs(os.path.join(artifacts_root, orphan), exist_ok=True)
        with open(os.path.join(artifacts_root, live, 'keep.txt'), 'w') as f:
            f.write('x')
        try:
            pruned = prune_artifact_dirs()
            self.assertGreaterEqual(pruned, 1)
            self.assertFalse(os.path.exists(
                os.path.join(artifacts_root, orphan)))
            self.assertTrue(os.path.isdir(os.path.join(artifacts_root, live)))
        finally:
            import shutil
            shutil.rmtree(os.path.join(artifacts_root, live),
                          ignore_errors=True)
            shutil.rmtree(os.path.join(artifacts_root, orphan),
                          ignore_errors=True)
            _purge(live)


def _purge(job_ids):
    if isinstance(job_ids, str):
        job_ids = [job_ids]
    marks = ','.join('?' for _ in job_ids)
    _sql(f'DELETE FROM recipe_jobs WHERE id IN ({marks})', job_ids)


if __name__ == '__main__':
    unittest.main()
