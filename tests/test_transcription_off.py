"""Speech to text can be switched off without ever loading Whisper.

A provider that cannot transcribe (Gemini, for example) used to leave the
job on the local model. On a small machine that model takes the process
down, so Off has to return before audio extraction or model load.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())

# The transcriber imports faster-whisper at module level. The Off path must
# not need the native library, so a stand-in is enough for this test.
sys.modules.setdefault('faster_whisper', MagicMock())

from config import config, set_config_value  # noqa: E402
from transcriber import Transcriber  # noqa: E402


class TranscriptionOffTests(unittest.TestCase):
    def setUp(self):
        set_config_value('transcription_mode', 'local')
        config.reload()

    def test_off_returns_empty_without_touching_audio_or_whisper(self):
        set_config_value('transcription_mode', 'off')
        config.reload()
        self.assertEqual(config.TRANSCRIPTION_MODE, 'off')

        transcriber = Transcriber('/tmp/no-such-video.mp4')
        transcriber._extract_audio = MagicMock(side_effect=AssertionError('audio'))
        transcriber._load_model = MagicMock(side_effect=AssertionError('whisper'))

        self.assertEqual(transcriber.transcribe(), '')
        transcriber._extract_audio.assert_not_called()
        transcriber._load_model.assert_not_called()

    def test_an_unknown_mode_stays_on_local_whisper(self):
        set_config_value('transcription_mode', 'sometimes')
        config.reload()
        self.assertEqual(config.TRANSCRIPTION_MODE, 'local')


if __name__ == '__main__':
    unittest.main()
