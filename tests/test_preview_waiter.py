"""Regression for PreviewWaiter construction when confirm-before-upload is on.

Issue #27: queueing any URL aborted immediately with
`PreviewWaiter.__init__() missing 1 required positional argument: 'is_cancelled'`.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _preview_waiter_calls(source: str) -> list[ast.Call]:
    tree = ast.parse(source)
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'PreviewWaiter'
    ]


class TestPreviewWaiterConstruction(unittest.TestCase):
    def test_process_video_job_passes_is_cancelled(self):
        """The UI worker is the only PreviewWaiter caller; keep it in sync."""
        source = (ROOT / 'ui' / 'app.py').read_text(encoding='utf-8')
        calls = _preview_waiter_calls(source)
        self.assertTrue(calls, 'process_video_job must construct PreviewWaiter')
        for call in calls:
            keywords = {kw.arg for kw in call.keywords}
            self.assertIn(
                'is_cancelled',
                keywords,
                'PreviewWaiter(...) is missing is_cancelled',
            )


if __name__ == '__main__':
    unittest.main()
