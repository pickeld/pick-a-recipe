"""Tests for job queue and auth utilities."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

_test_dir = tempfile.mkdtemp()
os.environ['DATA_DIR'] = _test_dir


class TestPasswordHashing(unittest.TestCase):
    def test_bcrypt_roundtrip(self):
        from database import hash_password, _verify_password_hash
        hashed = hash_password('securepass123')
        self.assertTrue(hashed.startswith('$2'))
        self.assertTrue(_verify_password_hash(hashed, 'securepass123'))
        self.assertFalse(_verify_password_hash(hashed, 'wrong'))

    def test_legacy_sha256_upgrade(self):
        import hashlib
        from database import _verify_password_hash
        legacy = hashlib.sha256(b'admin123').hexdigest()
        self.assertTrue(_verify_password_hash(legacy, 'admin123'))


class TestQueuePosition(unittest.TestCase):
    def test_queue_position_ordering(self):
        from database import create_job, get_queue_position, get_db, init_db
        init_db()
        j1 = create_job('https://example.com/1')
        j2 = create_job('https://example.com/2')
        self.assertEqual(get_queue_position(j1), 1)
        self.assertEqual(get_queue_position(j2), 2)
        with get_db() as conn:
            conn.execute("DELETE FROM recipe_jobs WHERE id IN (?, ?)", (j1, j2))
            conn.commit()


class TestMaxConcurrent(unittest.TestCase):
    def test_resolve_bounds(self):
        from job_manager import resolve_max_concurrent
        os.environ['MAX_CONCURRENT_JOBS'] = '8'
        self.assertEqual(resolve_max_concurrent(), 8)
        os.environ['MAX_CONCURRENT_JOBS'] = '99'
        self.assertEqual(resolve_max_concurrent(), 16)
        del os.environ['MAX_CONCURRENT_JOBS']


if __name__ == '__main__':
    unittest.main()
