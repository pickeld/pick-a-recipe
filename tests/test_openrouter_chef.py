"""Regression test for OpenRouter recipe generation."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from chef import Chef  # noqa: E402
from config import config  # noqa: E402


class OpenRouterChefTests(unittest.TestCase):
    def test_recipe_call_uses_chat_completions_with_json_schema(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))
            ]
        )
        settings = {
            "llm_provider": "openrouter",
            "openrouter_model": "openai/gpt-4o-mini",
            "openrouter_api_key": "test-key",
        }

        def call_configured_model(provider, model, call):
            self.assertEqual(provider, "openrouter")
            return call(model), model

        with (
            patch.object(config, "_db_config", settings),
            patch("llm_openrouter.make_openrouter_client", return_value=client),
            patch(
                "chef.call_with_model_fallback",
                side_effect=call_configured_model,
            ),
        ):
            chef = Chef(
                source_url="https://example.com/recipe",
                description="",
                transcription="",
            )
            result = chef._call_llm(
                "system prompt",
                "recipe input",
                schema_name="recipe",
                json_schema={"type": "object"},
            )

        self.assertEqual(result, '{"ok": true}')
        client.chat.completions.create.assert_called_once_with(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "recipe input"},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "recipe",
                    "strict": True,
                    "schema": {"type": "object"},
                },
            },
        )
