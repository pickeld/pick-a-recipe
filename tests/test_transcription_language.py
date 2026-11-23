"""Speech is transcribed in the language it is spoken in, not the target language.

target_language names the language the recipe is written in. Handing it to
Whisper as the spoken language made an English video with target "de" come
back as a garbled German transcript, and the recipe was built from that.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())

# Whisper itself is not needed: the test only looks at what it is asked to do.
sys.modules.setdefault('faster_whisper', MagicMock())

from config import config, set_config_value  # noqa: E402
from transcriber import Transcriber  # noqa: E402


def _transcriber():
    transcriber = Transcriber('/tmp/no-such-video.mp4')
    transcriber._extract_audio = MagicMock(return_value='/tmp/no-such-audio.wav')
    transcriber._load_model = MagicMock()
    transcriber.model = MagicMock()
    transcriber.model.transcribe.return_value = ([], None)
    return transcriber


class TranscriptionLanguageTests(unittest.TestCase):
    def setUp(self):
        set_config_value('transcription_mode', 'local')
        set_config_value('target_language', 'de')
        config.reload()

    def tearDown(self):
        set_config_value('target_language', '')
        config.reload()

    def test_local_whisper_detects_the_spoken_language(self):
        transcriber = _transcriber()
        with patch.object(Transcriber, '_transcribe_with_provider', return_value=None):
            transcriber.transcribe()
        self.assertIsNone(transcriber.model.transcribe.call_args.kwargs['language'])

    def test_provider_is_not_told_the_target_language(self):
        transcriber = _transcriber()
        with patch.object(Transcriber, '_transcribe_with_provider', return_value='hello') as provider:
            self.assertEqual(transcriber.transcribe(), 'hello')
        self.assertIsNone(provider.call_args.args[1])

    def test_an_explicit_language_is_still_used(self):
        transcriber = _transcriber()
        with patch.object(Transcriber, '_transcribe_with_provider', return_value=None):
            transcriber.transcribe(language='en')
        self.assertEqual(transcriber.model.transcribe.call_args.kwargs['language'], 'en')

    def test_blank_language_is_not_forwarded(self):
        transcriber = _transcriber()
        with patch.object(Transcriber, '_transcribe_with_provider', return_value=None):
            transcriber.transcribe(language='  ')
        self.assertIsNone(transcriber.model.transcribe.call_args.kwargs['language'])

    def test_audio_cache_is_not_named_after_the_recipe_language(self):
        path = os.path.join(ROOT, 'pipeline.py')
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
        self.assertIn('os.path.join(dish_dir, "transcription.txt")', text)
        self.assertNotIn('transcription_{', text)


if __name__ == '__main__':
    unittest.main()
