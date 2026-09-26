"""The settings page's side of the AI provider registry.

The plain-HTML settings form posts one parallel list per column rather than
JSON, and those lists are the only way a browser without the SPA bundle can
configure providers at all - so the rebuild deserves its own coverage.
"""

import json
import os
import sys
import tempfile
import unittest

from werkzeug.datastructures import MultiDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())


def _row(name, api_type, key='', base_url='', model='', provider_id=''):
    return [
        ('provider_id[]', provider_id),
        ('provider_name[]', name),
        ('provider_api_type[]', api_type),
        ('provider_api_key[]', key),
        ('provider_base_url[]', base_url),
        ('provider_model[]', model),
    ]


class ProvidersFromFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import _providers_from_form
        cls.rebuild = staticmethod(_providers_from_form)

    def _rebuild(self, *rows):
        pairs = [pair for row in rows for pair in row]
        return json.loads(self.rebuild(MultiDict(pairs)))

    def test_rebuilds_rows_in_order(self):
        result = self._rebuild(
            _row('OpenAI', 'openai', key='sk-a', model='gpt-5-mini',
                 provider_id='openai'),
            _row('Local Ollama', 'openai-compatible',
                 base_url='http://localhost:11434/v1', model='llama3.2-vision'),
        )
        self.assertEqual([p['id'] for p in result], ['openai', 'local-ollama'])
        self.assertEqual(result[1]['base_url'], 'http://localhost:11434/v1')
        # A local gateway issues no key, and must not be rejected for it.
        self.assertEqual(result[1]['api_key'], '')

    def test_an_untouched_blank_row_is_dropped(self):
        """The page keeps a template row around; saving must not store it."""
        result = self._rebuild(
            _row('OpenAI', 'openai', key='sk-a', model='gpt-5-mini'),
            _row('', 'openai'),
        )
        self.assertEqual(len(result), 1)

    def test_ids_are_derived_from_names_and_stay_unique(self):
        result = self._rebuild(
            _row('My Gateway', 'openai-compatible',
                 base_url='https://a.test/v1', model='m1'),
            _row('My Gateway', 'openai-compatible',
                 base_url='https://b.test/v1', model='m2'),
        )
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]['id'], result[1]['id'])


class SettingsRoundTripTests(unittest.TestCase):
    """Saving the form has to produce a registry the rest of the app can read."""

    @classmethod
    def setUpClass(cls):
        os.environ['DATA_DIR'] = tempfile.mkdtemp()
        import config as config_module
        import database
        from app import app

        config_module.DATA_DIR = os.environ['DATA_DIR']
        app.config['TESTING'] = True
        database.init_db()
        database.create_local_user('tester', 'hash', is_admin=True)
        cls.app = app
        cls.database = database

    def _client(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session['user'] = 'tester'
            session['is_admin'] = True
        return client

    def test_saved_providers_are_what_extraction_then_uses(self):
        import ai_providers
        from config import config

        client = self._client()
        response = client.post('/settings', data=MultiDict(
            _row('OpenAI', 'openai', key='sk-a', model='gpt-5-mini',
                 provider_id='openai')
            + _row('Gateway', 'openai-compatible', key='sk-g',
                   base_url='https://gateway.test/v1', model='some/model')
            + [
                ('ai_extraction_provider', 'gateway'),
                ('transcription_mode', 'provider'),
                ('transcription_provider', 'openai'),
                ('transcription_model', 'whisper-1'),
            ]
        ))
        self.assertEqual(response.status_code, 302)

        config.reload()
        self.assertEqual(ai_providers.selected_provider().id, 'gateway')
        self.assertEqual(
            ai_providers.selected_provider().base_url, 'https://gateway.test/v1')
        self.assertEqual(ai_providers.transcription_provider().id, 'openai')

    def test_a_transcription_provider_that_vanished_falls_back_to_whisper(self):
        """Losing the provider must not lose the audio."""
        import ai_providers
        from config import config

        client = self._client()
        client.post('/settings', data=MultiDict(
            _row('OpenAI', 'openai', key='sk-a', model='gpt-5-mini',
                 provider_id='openai')
            + [
                ('ai_extraction_provider', 'openai'),
                ('transcription_mode', 'provider'),
                ('transcription_provider', 'deleted-gateway'),
                ('transcription_model', 'whisper-1'),
            ]
        ))
        config.reload()
        self.assertIsNone(ai_providers.transcription_provider())

    def test_the_settings_page_renders_the_new_sections(self):
        body = self._client().get('/settings').get_data(as_text=True)
        self.assertIn('AI Providers', body)
        self.assertIn('AI Extraction', body)
        self.assertIn('Speech to Text', body)
        self.assertIn('provider_api_type[]', body)

    def test_the_api_serves_the_registry_and_the_dialects(self):
        payload = self._client().get('/api/ai/providers').get_json()
        self.assertTrue(payload['providers'])
        self.assertIn('openai-compatible', payload['api_types'])
        self.assertTrue(payload['api_types']['openai-compatible']['needs_base_url'])


if __name__ == '__main__':
    unittest.main()
