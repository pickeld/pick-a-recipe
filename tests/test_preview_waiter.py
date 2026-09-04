"""Regression for PreviewWaiter construction when confirm-before-upload is on.

Issue #27: queueing any URL aborted immediately with
`PreviewWaiter.__init__() missing 1 required positional argument: 'is_cancelled'`.
"""

import ast
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'ui'))

from app_harness import result as _result, run as _run  # noqa: E402


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

    def test_process_video_job_builds_preview_waiter(self):
        """The exact #27 crash: worker starts, confirm-before-upload is on."""
        script = textwrap.dedent("""
            import sys
            from unittest.mock import MagicMock, patch

            for name in (
                'faster_whisper', 'yt_dlp', 'yt_dlp.utils',
                'openai', 'google.genai',
            ):
                sys.modules.setdefault(name, MagicMock())

            from app import process_video_job
            from database import create_job, get_job
            from job_manager import get_job_manager
            from pipeline import PipelineResult

            job_id = create_job('https://example.com/issue-27', user_id='alice')
            jm = get_job_manager()
            seen = {}

            def fake_pipeline(url, reporter, **kwargs):
                preview = kwargs['preview']
                seen['has_preview'] = preview is not None
                seen['cancelled'] = preview.is_cancelled()
                return PipelineResult(awaiting_approval=True)

            with patch('pipeline.run_url_pipeline', fake_pipeline):
                process_video_job(job_id, jm)

            job = get_job(job_id)
            emit(
                error=job.get('error_message'),
                status=job.get('status'),
                **seen,
            )
        """)
        out = _result(_run(script))
        self.assertTrue(out.get('has_preview'), out)
        self.assertFalse(out.get('cancelled'), out)
        self.assertIsNone(out.get('error'))


if __name__ == '__main__':
    unittest.main()
