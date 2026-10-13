"""The four flows that used to reach users before a test did.

Each one runs through the same door the UI uses: POST /api/config, the job
worker, and POST /api/pending-uploads/<id>/confirm. Gemini and Mealie are
fakes. Nothing here calls a real API.
"""

import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app_harness import result as _result, run as _run  # noqa: E402


_SESSION = textwrap.dedent("""
    from app import app, process_video_job
    from database import create_local_user

    create_local_user('tester', 'hash', is_admin=True)
    client = app.test_client()
    with client.session_transaction() as session:
        session['user'] = 'tester'
        session['is_admin'] = True
""")


class ReportedFlowTests(unittest.TestCase):
    def test_renamed_provider_is_what_a_refresh_reads(self):
        """Change the name, save, refresh. The old name must not come back."""
        script = _SESSION + textwrap.dedent("""
            import json
            from ai_providers import load_providers
            from config import config

            renamed = [{
                'id': 'gemini',
                'name': 'Renamed Gemini',
                'api_type': 'gemini',
                'api_key': 'gk',
                'base_url': '',
                'model': 'gemini-3.6-flash',
            }]
            saved = client.post('/api/config', json={
                'ai_providers': json.dumps(renamed),
            })
            # The page reads the process snapshot. Do not reload it here:
            # the save itself has to have done that.
            after_save = [provider.name for provider in load_providers()]

            config._db_config['ai_providers'] = json.dumps(
                [{**renamed[0], 'name': 'Gemini'}])
            refreshed = client.get('/api/ai/providers').get_json()
            after_refresh = [provider['name'] for provider in refreshed['providers']]

            client.post('/api/config', json={'recipe_lang': 'english'})
            after_other_save = [
                provider['name']
                for provider in client.get('/api/ai/providers').get_json()['providers']
            ]

            renamed[0]['api_key'] = ''
            client.post('/api/config', json={'ai_providers': json.dumps(renamed)})
            kept = client.get('/api/ai/providers').get_json()['providers']
            gemini = next(provider for provider in kept if provider['id'] == 'gemini')

            emit(
                status=saved.status_code,
                after_save=after_save,
                after_refresh=after_refresh,
                after_other_save=after_other_save,
                key=gemini['api_key'],
                name=gemini['name'],
            )
        """)
        out = _result(_run(script))
        self.assertEqual(out['status'], 200, out)
        self.assertIn('Renamed Gemini', out['after_save'])
        self.assertEqual(out['after_refresh'], ['Renamed Gemini'])
        self.assertEqual(out['after_other_save'], ['Renamed Gemini'])
        self.assertEqual(out['name'], 'Renamed Gemini')
        self.assertEqual(out['key'], 'gk')

    def test_preview_follows_the_saved_switch_on_every_page(self):
        """Preview-first, saved in the UI, must open even off the home page."""
        script = _SESSION + textwrap.dedent("""
            import sys
            from unittest.mock import MagicMock, patch

            for name in (
                'faster_whisper', 'yt_dlp', 'yt_dlp.utils',
                'openai', 'google.genai',
            ):
                sys.modules.setdefault(name, MagicMock())

            from config import config
            from job_manager import RUNNING, QUEUED, get_job_manager
            from pipeline import PipelineResult

            client.post('/api/config', json={'confirm_before_upload': True})
            # Stale process snapshot still says the switch is off.
            config._db_config['confirm_before_upload'] = 'false'

            jm = get_job_manager()
            seen = {}

            def fake_pipeline(url, reporter, **kwargs):
                seen['preview'] = kwargs.get('preview') is not None
                return PipelineResult(awaiting_approval=True)

            job_on = jm.create_new_job('https://example.com/preview-on', user_id='tester')
            with patch('pipeline.run_url_pipeline', fake_pipeline):
                process_video_job(job_on, jm)
            preview_when_saved_on = seen.get('preview')

            client.post('/api/config', json={'confirm_before_upload': False})
            config._db_config['confirm_before_upload'] = 'true'
            job_off = jm.create_new_job('https://example.com/preview-off', user_id='tester')
            with patch('pipeline.run_url_pipeline', fake_pipeline):
                process_video_job(job_off, jm)
            preview_when_saved_off = seen.get('preview')

            parked = jm.create_new_job('https://example.com/parked', user_id='tester')
            jm.transition(parked, RUNNING, expected_old=QUEUED)
            jm.open_approval(parked, {'name': 'Preview Soup'}, None, [], 'mealie')
            listing = client.get('/api/pending-uploads').get_json()
            uploads = listing['pending_uploads']

            emit(
                preview_when_saved_on=preview_when_saved_on,
                preview_when_saved_off=preview_when_saved_off,
                names=[item['recipe']['name'] for item in uploads],
                expires=[item.get('expires_at') for item in uploads],
            )
        """)
        out = _result(_run(script))
        self.assertTrue(out['preview_when_saved_on'], out)
        self.assertFalse(out['preview_when_saved_off'], out)
        self.assertIn('Preview Soup', out['names'])
        self.assertEqual(out['expires'], [None])

    def test_approve_reaches_mealie_and_does_not_expire(self):
        """Approve, and the recipe is uploaded. The sweeper must not expire it."""
        script = _SESSION + textwrap.dedent("""
            import threading
            import time
            from unittest.mock import patch

            from database import get_job, get_pending_upload
            from job_manager import RUNNING, QUEUED, get_job_manager

            client.post('/api/config', json={
                'mealie_enabled': True,
                'mealie_host': 'http://mealie.invalid',
                'mealie_api_key': 'mealie-test-key',
                'tandoor_enabled': False,
            })

            jm = get_job_manager()
            job_id = jm.create_new_job('https://example.com/shakshuka', user_id='tester')
            jm.transition(job_id, RUNNING, expected_old=QUEUED)
            upload_id = jm.open_approval(
                job_id, {'name': 'Shakshuka'}, None, [], 'mealie')

            # The window that used to expire the job: confirmed, upload worker
            # not in the upload yet. Hold it there, then sweep.
            saved_resume = jm._resume_func
            jm._resume_func = None
            confirmed = client.post(
                f'/api/pending-uploads/{upload_id}/confirm', json={})
            time.sleep(0.4)
            jm.sweep_once()
            held_job = get_job(job_id)['status']
            held_upload = get_pending_upload(upload_id)['status']

            reached = False
            during = {'status': None, 'error_message': None}
            during_sweep = None
            final = during
            called = threading.Event()
            release = threading.Event()
            seen = {}

            class FakeMealie:
                def __init__(self, *args, **kwargs):
                    pass

                def create_recipe(self, recipe):
                    seen['name'] = recipe.get('name')
                    called.set()
                    release.wait(timeout=8)
                    return {'slug': 'shakshuka', 'id': '1'}

                def upload_image(self, *args, **kwargs):
                    return None

            try:
                with patch('mealie.Mealie', FakeMealie):
                    jm._resume_func = saved_resume
                    reached = called.wait(timeout=5)
                    during = get_job(job_id)
                    jm.sweep_once()
                    during_sweep = get_job(job_id)['status']
                    release.set()
                    deadline = time.time() + 5
                    final = during
                    while time.time() < deadline:
                        final = get_job(job_id)
                        if final['status'] in ('completed', 'failed', 'expired'):
                            break
                        time.sleep(0.05)
            finally:
                release.set()
                jm._resume_func = saved_resume

            emit(
                confirm_status=confirmed.status_code,
                held_job=held_job,
                held_upload=held_upload,
                reached=reached,
                recipe=seen.get('name'),
                during=during['status'],
                during_error=during.get('error_message'),
                during_sweep=during_sweep,
                final=final['status'],
                final_error=final.get('error_message'),
            )
        """)
        out = _result(_run(script))
        self.assertEqual(out['confirm_status'], 200, out)
        self.assertEqual(out['held_job'], 'awaiting_approval', out)
        self.assertEqual(out['held_upload'], 'confirmed', out)
        self.assertTrue(out['reached'], out)
        self.assertEqual(out['recipe'], 'Shakshuka', out)
        self.assertEqual(out['during'], 'uploading', out)
        self.assertEqual(out['during_sweep'], 'uploading', out)
        self.assertEqual(out['final'], 'completed', out)
        self.assertNotEqual(out['final'], 'expired', out)


if __name__ == '__main__':
    unittest.main()
