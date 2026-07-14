"""Tests for the LLM resilience layer (llm_resilience.py).

Run with: python -m unittest discover -s tests
These cover the pure logic - error classification, candidate ordering, and the
fallback control flow - without needing the provider SDKs or network.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_resilience import (  # noqa: E402
    ModelUnavailableError,
    call_with_model_fallback,
    candidate_models,
    is_model_unavailable_error,
)


class _FakeStatusError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class IsModelUnavailableErrorTests(unittest.TestCase):
    def test_404_status_code(self):
        self.assertTrue(is_model_unavailable_error(_FakeStatusError("boom", 404)))

    def test_404_in_message(self):
        self.assertTrue(is_model_unavailable_error(
            Exception("404 NOT_FOUND: model models/gemini-2.0-flash is no longer available")))

    def test_deprecated_marker(self):
        self.assertTrue(is_model_unavailable_error(Exception("This model is deprecated")))

    def test_model_not_found_marker(self):
        self.assertTrue(is_model_unavailable_error(Exception("model_not_found")))

    def test_unrelated_error_is_not_model_unavailable(self):
        self.assertFalse(is_model_unavailable_error(Exception("connection reset by peer")))

    def test_auth_error_is_not_model_unavailable(self):
        self.assertFalse(is_model_unavailable_error(_FakeStatusError("invalid api key", 401)))


class CandidateModelsTests(unittest.TestCase):
    def test_configured_model_is_first(self):
        cands = candidate_models("gemini", "gemini-2.5-flash")
        self.assertEqual(cands[0], "gemini-2.5-flash")

    def test_custom_model_prepended_to_fallbacks(self):
        cands = candidate_models("openai", "gpt-custom")
        self.assertEqual(cands[0], "gpt-custom")
        self.assertIn("gpt-4o-mini", cands)

    def test_no_duplicates(self):
        cands = candidate_models("gemini", "gemini-2.5-flash")
        self.assertEqual(len(cands), len(set(cands)))

    def test_unknown_provider_returns_just_configured(self):
        self.assertEqual(candidate_models("mystery", "m1"), ["m1"])


class CallWithModelFallbackTests(unittest.TestCase):
    def test_first_model_succeeds(self):
        calls = []

        def call(model):
            calls.append(model)
            return f"ok:{model}"

        result, used = call_with_model_fallback(
            "openai", "gpt-5-mini-2025-08-07", call, persist=False)
        self.assertEqual(result, "ok:gpt-5-mini-2025-08-07")
        self.assertEqual(used, "gpt-5-mini-2025-08-07")
        self.assertEqual(calls, ["gpt-5-mini-2025-08-07"])

    def test_falls_over_to_next_model_on_404(self):
        attempted = []

        def call(model):
            attempted.append(model)
            if model == "gemini-2.0-flash":
                raise Exception("404 model models/gemini-2.0-flash is no longer available")
            return "recovered"

        result, used = call_with_model_fallback(
            "gemini", "gemini-2.0-flash", call, persist=False)
        self.assertEqual(result, "recovered")
        self.assertEqual(used, "gemini-2.5-flash")
        self.assertEqual(attempted[:2], ["gemini-2.0-flash", "gemini-2.5-flash"])

    def test_non_model_error_is_reraised_immediately(self):
        attempted = []

        def call(model):
            attempted.append(model)
            raise ValueError("bad content")

        with self.assertRaises(ValueError):
            call_with_model_fallback("openai", "gpt-4o-mini", call, persist=False)
        # Should not have churned through fallbacks for a real error.
        self.assertEqual(attempted, ["gpt-4o-mini"])

    def test_all_models_unavailable_raises_actionable_error(self):
        def call(model):
            raise Exception("404 not found")

        with self.assertRaises(ModelUnavailableError) as ctx:
            call_with_model_fallback("openai", "gpt-dead", call, persist=False)
        msg = str(ctx.exception)
        self.assertIn("openai", msg)
        self.assertIn("Settings", msg)


if __name__ == "__main__":
    unittest.main()
