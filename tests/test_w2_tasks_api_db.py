"""Tests for Wave-2 task-listing DB helpers (list/count/priority)."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir


def _purge(job_ids):
    from database import get_db
    with get_db() as conn:
        conn.executemany(
            'DELETE FROM recipe_jobs WHERE id = ?', [(j,) for j in job_ids]
        )
        conn.commit()


class TestListJobsByStates(unittest.TestCase):
    def _make(self):
        from database import create_job, get_db, get_queued_jobs
        a = create_job('https://example.com/lq', user_id='alice')
        b = create_job('https://example.com/lr', user_id='alice')
        c = create_job('https://example.com/lo', user_id='bob')
        with get_db() as conn:
            conn.execute("UPDATE recipe_jobs SET status='running' WHERE id=?", (b,))
            conn.commit()
        return a, b, c

    def test_state_filter_and_scoping(self):
        from database import list_jobs_by_states
        a, b, c = self._make()
        try:
            alice_view = {j['id'] for j in list_jobs_by_states(
                ['queued', 'running'], user_id='alice')}
            self.assertEqual(alice_view, {a, b})

            admin_view = {j['id'] for j in list_jobs_by_states(
                ['running'], is_admin=True)}
            self.assertIn(b, admin_view)
            self.assertNotIn(a, admin_view)

            bob_view = {j['id'] for j in list_jobs_by_states(
                ['queued'], user_id='bob')}
            self.assertEqual(bob_view, {c})
        finally:
            _purge([a, b, c])

    def test_queue_position_enriched(self):
        from database import list_jobs_by_states
        a, b, c = self._make()
        try:
            row = next(j for j in list_jobs_by_states(['queued'])
                       if j['id'] == a)
            self.assertEqual(row['queue_position'], 1)
        finally:
            _purge([a, b, c])

    def test_awaiting_approval_enriched_with_upload(self):
        from database import (
            create_job, create_pending_upload, list_jobs_by_states,
        )
        jid = create_job('https://example.com/ap2', user_id='alice')
        up = 'up_' + os.urandom(8).hex()
        create_pending_upload(
            upload_id=up, job_id=jid, recipe_data={'name': 'E'},
            image_path=None, image_candidates=[], output_target='mealie',
            best_image_index=0, timeout_minutes=5, user_id='alice',
        )
        try:
            from database import get_db
            with get_db() as conn:
                conn.execute(
                    "UPDATE recipe_jobs SET status='awaiting_approval' "
                    'WHERE id = ?', (jid,))
                conn.commit()

            row = next(j for j in list_jobs_by_states(['awaiting_approval'],
                                                      user_id='alice')
                       if j['id'] == jid)
            self.assertEqual(row['pending_upload_id'], up)
            self.assertIsNone(row['approval_expires_at'])
        finally:
            _purge([jid])
            from database import get_db
            with get_db() as conn:
                conn.execute('DELETE FROM pending_uploads WHERE id = ?', (up,))
                conn.commit()


class TestCountJobsByStates(unittest.TestCase):
    def test_counts_scoped(self):
        from database import count_jobs_by_states, create_job, get_db
        a = create_job('https://example.com/c1', user_id='alice')
        b = create_job('https://example.com/c2', user_id='bob')
        try:
            alice_counts = count_jobs_by_states(user_id='alice')
            self.assertGreaterEqual(alice_counts.get('queued', 0), 1)
            total_alice = sum(alice_counts.values())
            admin_counts = count_jobs_by_states(is_admin=True)
            self.assertGreaterEqual(sum(admin_counts.values()), total_alice)
        finally:
            _purge([a, b])


class TestUpdateJobPriority(unittest.TestCase):
    def test_priority_only_on_queued_and_scoped(self):
        from database import (
            create_job, get_db, update_job_priority,
        )
        jid = create_job('https://example.com/pr', user_id='alice')
        other = create_job('https://example.com/pr2', user_id='bob')
        try:
            # Wrong owner denied.
            self.assertFalse(update_job_priority(jid, 5, user_id='bob'))
            # Owner succeeds.
            self.assertTrue(update_job_priority(jid, 5, user_id='alice'))
            with get_db() as conn:
                val = conn.execute(
                    'SELECT queue_priority FROM recipe_jobs WHERE id=?',
                    (jid,)).fetchone()[0]
            self.assertEqual(val, 5)

            # Running job cannot be reordered.
            with get_db() as conn:
                conn.execute("UPDATE recipe_jobs SET status='running' WHERE id=?",
                             (other,))
                conn.commit()
            self.assertFalse(update_job_priority(other, 9, user_id='bob',
                                                 is_admin=True))
        finally:
            _purge([jid, other])


if __name__ == '__main__':
    unittest.main()
