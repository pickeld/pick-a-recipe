"""Tests for the settings-page model suggestions (model_catalog.py)."""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import model_catalog  # noqa: E402

OPENROUTER = {"data": [
    {
        "id": "google/gemini-3.6-flash", "name": "Google: Gemini 3.6 Flash", "created": 1790000000,
        "context_length": 1048576,
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "0.0000003", "completion": "0.0000025"},
        "supported_parameters": ["response_format", "structured_outputs"],
    },
    {
        "id": "openrouter/auto", "name": "Auto Router", "created": 1795000000,
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "-1", "completion": "-1"},
        "supported_parameters": ["structured_outputs"],
    },
    {
        "id": "text/only", "name": "Text only", "created": 1796000000,
        "architecture": {"input_modalities": ["text"]},
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": ["structured_outputs"],
    },
    {
        "id": "no/schema", "name": "No schema", "created": 1796000000,
        "architecture": {"input_modalities": ["text", "image"]},
        "pricing": {"prompt": "0", "completion": "0"},
        "supported_parameters": ["response_format"],
    },
]}

MODELS_DEV = {
    "openrouter": {"models": {
        "fallback/model": {
            "name": "From models.dev", "release_date": "2026-01-01",
            "modalities": {"input": ["text", "image"]}, "structured_output": True,
        },
    }},
    "google": {"models": {
        "gemini-3.6-flash": {
            "name": "Gemini 3.6 Flash", "release_date": "2026-06-01",
            "modalities": {"input": ["text", "image"]}, "structured_output": True,
            "cost": {"input": 0.3, "output": 2.5}, "limit": {"context": 1048576},
        },
        "gemini-tts": {
            "name": "TTS", "release_date": "2026-07-01",
            "modalities": {"input": ["text"]}, "structured_output": False,
        },
    }},
}


def _fake_fetch(url):
    return OPENROUTER if url == model_catalog._OPENROUTER_URL else MODELS_DEV


class ModelCatalogTests(unittest.TestCase):
    def setUp(self):
        model_catalog._cache.clear()

    def test_openrouter_comes_from_its_own_api_with_per_million_prices(self):
        with patch.object(model_catalog, "_fetch", side_effect=_fake_fetch):
            models = model_catalog.catalog_models("openrouter")
        self.assertEqual([m["id"] for m in models], ["openrouter/auto", "google/gemini-3.6-flash"])
        flash = models[1]
        self.assertAlmostEqual(flash["input_cost"], 0.3)
        self.assertAlmostEqual(flash["output_cost"], 2.5)
        self.assertEqual(flash["context"], 1048576)
        self.assertRegex(flash["released"], r"^\d{4}-\d{2}-\d{2}$")

    def test_variable_price_is_unknown_not_negative(self):
        with patch.object(model_catalog, "_fetch", side_effect=_fake_fetch):
            auto = model_catalog.catalog_models("openrouter")[0]
        self.assertIsNone(auto["input_cost"])
        self.assertIsNone(auto["output_cost"])

    def test_openrouter_outage_falls_back_to_models_dev(self):
        def fetch(url):
            if url == model_catalog._OPENROUTER_URL:
                raise ConnectionError("down")
            return MODELS_DEV

        with patch.object(model_catalog, "_fetch", side_effect=fetch):
            models = model_catalog.catalog_models("openrouter")
        self.assertEqual([m["id"] for m in models], ["fallback/model"])
        self.assertIsNone(models[0]["input_cost"])

    def test_models_dev_providers_carry_their_prices(self):
        with patch.object(model_catalog, "_fetch", side_effect=_fake_fetch):
            models = model_catalog.catalog_models("google")
        self.assertEqual(models, [{
            "id": "gemini-3.6-flash", "name": "Gemini 3.6 Flash", "released": "2026-06-01",
            "input_cost": 0.3, "output_cost": 2.5, "context": 1048576, "unit": "mtok",
        }])

    def test_transcription_models_are_priced_per_minute_or_per_token(self):
        listing = {"data": [
            {"id": "openai/gpt-4o-transcribe", "name": "GPT-4o Transcribe",
             "pricing": {"prompt": "0.0000025", "completion": "0.00001"}},
            {"id": "openai/whisper-1", "name": "Whisper 1",
             "pricing": {"prompt": "0.0001", "completion": "0"}},
        ]}

        def fetch(url):
            self.assertIn("output_modalities=transcription", url)
            return listing

        with patch.object(model_catalog, "_fetch", side_effect=fetch):
            models = model_catalog.catalog_models("openrouter", "transcription")
        whisper, gpt = models
        self.assertEqual((whisper["id"], whisper["unit"]), ("openai/whisper-1", "minute"))
        self.assertAlmostEqual(whisper["input_cost"], 0.006)
        self.assertEqual((gpt["unit"], gpt["input_cost"], gpt["output_cost"]), ("mtok", 2.5, 10.0))

    def test_only_openrouter_lists_transcription_models(self):
        with patch.object(model_catalog, "_fetch", side_effect=AssertionError("no fetch")):
            self.assertEqual(model_catalog.catalog_models("openai", "transcription"), [])

    def test_documents_are_cached(self):
        with patch.object(model_catalog, "_fetch", side_effect=_fake_fetch) as fetch:
            model_catalog.catalog_models("openrouter")
            model_catalog.catalog_models("openrouter")
        self.assertEqual(fetch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
