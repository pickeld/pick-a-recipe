"""Verbose logging for bug reports.

The level is a setting, the recent lines are what Settings copies, and a
credential that reached a message must not survive into that copy. People
paste the copy into a public issue.
"""

import logging
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ui'))

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp())


class LogLevelTests(unittest.TestCase):
    def test_names_collapse_onto_three_levels(self):
        from helpers import normalize_log_level

        self.assertEqual(normalize_log_level('verbose'), 'debug')
        self.assertEqual(normalize_log_level('Quiet'), 'warning')
        self.assertEqual(normalize_log_level(''), 'info')
        self.assertEqual(normalize_log_level('nope'), 'info')

    def test_verbose_keeps_debug_lines_and_quiet_drops_them(self):
        from helpers import (
            apply_log_level, clear_recent_logs, recent_log_text, setup_logger,
        )

        apply_log_level('warning')
        clear_recent_logs()
        log = setup_logger('verbosity-test')
        log.info('hidden-info-line')
        log.warning('visible-warn-line')
        quiet = recent_log_text()
        self.assertNotIn('hidden-info-line', quiet)
        self.assertIn('visible-warn-line', quiet)
        self.assertIn('log_level: warning', quiet)

        apply_log_level('debug')
        log.debug('visible-debug-line')
        verbose = recent_log_text()
        self.assertIn('visible-debug-line', verbose)
        self.assertIn('log_level: debug', verbose)

    def test_level_comes_from_the_environment(self):
        from helpers import apply_log_level

        previous = os.environ.get('LOG_LEVEL')
        os.environ['LOG_LEVEL'] = 'verbose'
        try:
            self.assertEqual(apply_log_level(), 'debug')
        finally:
            if previous is None:
                os.environ.pop('LOG_LEVEL', None)
            else:
                os.environ['LOG_LEVEL'] = previous
            apply_log_level()


class RedactionTests(unittest.TestCase):
    def test_patterns_and_stored_keys_are_stripped(self):
        from config import config, set_config_value
        from helpers import (
            apply_log_level, clear_recent_logs, recent_log_text, redact_text,
            setup_logger,
        )

        # A stand-in value, not a real credential. Restored afterwards so a
        # shared test database does not keep it.
        secret = 'mealie-secret-value-99'
        previous = config.MEALIE_API_KEY
        self.assertTrue(set_config_value('mealie_api_key', secret))
        config.reload()
        try:
            sample = (
                f'posted {secret} Authorization: Bearer abcdefghijklmnop '
                'api_key=sk-test-1234567890 '
                'https://mealie.example/api?token=query-secret-value'
            )
            cleaned = redact_text(sample)
            self.assertNotIn(secret, cleaned)
            self.assertNotIn('abcdefghijklmnop', cleaned)
            self.assertNotIn('sk-test-1234567890', cleaned)
            self.assertNotIn('query-secret-value', cleaned)
            self.assertIn('[redacted]', cleaned)

            clear_recent_logs()
            apply_log_level('info')
            setup_logger('redact-test').info('upload failed using %s', secret)
            captured = recent_log_text()
            self.assertNotIn(secret, captured)
            self.assertIn('[redacted]', captured)
        finally:
            set_config_value('mealie_api_key', previous)
            config.reload()


if __name__ == '__main__':
    logging.disable(logging.NOTSET)
    unittest.main()
