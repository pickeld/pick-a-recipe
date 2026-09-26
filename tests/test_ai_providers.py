"""The configurable AI provider registry and the dialects it speaks."""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ai_providers  # noqa: E402
from ai_providers import (  # noqa: E402
    ANTHROPIC,
    GEMINI,
    OPENAI,
    OPENAI_COMPATIBLE,
    AiProvider,
    AiSession,
    ProviderConfigError,
    load_providers,
    parse_providers,
    selected_provider,
    serialize_providers,
    slugify,
    validate,
)
from config import config  # noqa: E402


def _settings(**overrides):
    """Patch the config table with a provider registry and nothing else."""
    return patch.object(config, "_db_config", dict(overrides))


class RegistryTests(unittest.TestCase):
    def test_round_trips_through_json(self):
        providers = [
            AiProvider(id="a", name="A", api_type=OPENAI, api_key="k", model="m"),
            AiProvider(id="b", name="B", api_type=OPENAI_COMPATIBLE,
                       api_key="k2", base_url="https://x.test/v1", model="m2"),
        ]
        restored = parse_providers(serialize_providers(providers))
        self.assertEqual(providers, restored)

    def test_a_single_broken_record_does_not_lose_the_rest(self):
        """Settings has to stay openable so the bad record can be fixed."""
        raw = json.dumps([
            {"id": "good", "name": "Good", "api_type": OPENAI, "model": "m"},
            {"id": "bad", "name": "Bad", "api_type": "telepathy", "model": "m"},
            "not even an object",
        ])
        self.assertEqual([p.id for p in parse_providers(raw)], ["good"])

    def test_unreadable_json_is_ignored_rather_than_raised(self):
        self.assertEqual(parse_providers("{not json"), [])

    def test_duplicate_ids_keep_the_first(self):
        raw = json.dumps([
            {"id": "x", "name": "First", "api_type": OPENAI, "model": "m"},
            {"id": "x", "name": "Second", "api_type": GEMINI, "model": "m"},
        ])
        self.assertEqual([p.name for p in parse_providers(raw)], ["First"])

    def test_ids_are_derived_from_the_name_when_absent(self):
        self.assertEqual(slugify("My Local Ollama!"), "my-local-ollama")
        self.assertEqual(
            parse_providers(json.dumps([
                {"name": "My Local Ollama", "api_type": OPENAI, "model": "m"},
            ]))[0].id,
            "my-local-ollama",
        )


class LegacyMigrationTests(unittest.TestCase):
    """Issue #38: an existing install must not have to be reconfigured."""

    def test_old_settings_become_provider_records(self):
        with _settings(
            llm_provider="openrouter",
            openai_api_key="sk-old", openai_model="gpt-4o-mini",
            gemini_api_key="", gemini_model="gemini-2.5-flash",
            openrouter_api_key="sk-or", openrouter_model="openai/gpt-4o-mini",
        ):
            providers = {p.id: p for p in load_providers(config)}

        self.assertEqual(set(providers), {"openai", "openrouter"})
        self.assertEqual(providers["openai"].api_type, OPENAI)
        self.assertEqual(providers["openai"].model, "gpt-4o-mini")
        # OpenRouter is not special any more: it is one OpenAI-compatible
        # endpoint among many, distinguished only by its base URL.
        self.assertEqual(providers["openrouter"].api_type, OPENAI_COMPATIBLE)
        self.assertEqual(
            providers["openrouter"].base_url, "https://openrouter.ai/api/v1")

    def test_the_old_choice_is_still_the_selected_one(self):
        with _settings(
            llm_provider="openrouter",
            openrouter_api_key="sk-or", openrouter_model="openai/gpt-4o-mini",
        ):
            self.assertEqual(selected_provider(config).id, "openrouter")

    def test_a_stored_registry_wins_over_the_old_settings(self):
        raw = serialize_providers([
            AiProvider(id="mine", name="Mine", api_type=GEMINI,
                       api_key="k", model="gemini-2.5-flash"),
        ])
        with _settings(ai_providers=raw, llm_provider="openai",
                       openai_api_key="sk-old", openai_model="gpt-4o-mini"):
            self.assertEqual([p.id for p in load_providers(config)], ["mine"])

    def test_a_selection_pointing_nowhere_falls_back_rather_than_failing(self):
        raw = serialize_providers([
            AiProvider(id="kept", name="Kept", api_type=OPENAI,
                       api_key="k", model="m"),
        ])
        with _settings(ai_providers=raw, ai_extraction_provider="deleted-one"):
            self.assertEqual(selected_provider(config).id, "kept")


class ValidationTests(unittest.TestCase):
    def test_openai_compatible_needs_a_base_url(self):
        provider = AiProvider(id="x", name="X", api_type=OPENAI_COMPATIBLE,
                              api_key="k", model="m")
        with self.assertRaises(ProviderConfigError) as ctx:
            validate(provider)
        self.assertIn("base URL", str(ctx.exception))

    def test_a_local_openai_compatible_server_needs_no_key(self):
        """Ollama, LM Studio and vLLM do not issue API keys."""
        validate(AiProvider(id="x", name="X", api_type=OPENAI_COMPATIBLE,
                            base_url="http://localhost:11434/v1", model="llama3"))

    def test_hosted_providers_need_a_key(self):
        with self.assertRaises(ProviderConfigError):
            validate(AiProvider(id="x", name="X", api_type=OPENAI, model="m"))

    def test_a_model_name_is_always_required(self):
        with self.assertRaises(ProviderConfigError):
            validate(AiProvider(id="x", name="X", api_type=OPENAI, api_key="k"))

    def test_a_session_tolerates_a_missing_key(self):
        """So a half-configured instance still boots and says so in health."""
        AiSession(AiProvider(id="x", name="X", api_type=OPENAI, model="m"))


class DialectTests(unittest.TestCase):
    """Each API type asks for structured JSON the way that API expects."""

    def _session(self, provider, client):
        session = AiSession(provider)
        session._client = client
        return session

    def test_openai_uses_the_responses_api_text_format(self):
        client = Mock()
        client.responses.create.return_value = SimpleNamespace(output_text='{"ok": 1}')
        session = self._session(
            AiProvider(id="o", name="O", api_type=OPENAI, api_key="k", model="gpt-x"),
            client)

        result = session.complete_json(
            "sys", "user", schema_name="recipe", json_schema={"type": "object"})

        self.assertEqual(result, '{"ok": 1}')
        client.responses.create.assert_called_once_with(
            model="gpt-x",
            input=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "user"},
            ],
            text={"format": {
                "type": "json_schema", "name": "recipe",
                "strict": True, "schema": {"type": "object"},
            }},
        )

    def test_openai_compatible_uses_chat_completions_with_response_format(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": 1}'))])
        session = self._session(
            AiProvider(id="or", name="OpenRouter", api_type=OPENAI_COMPATIBLE,
                       api_key="k", base_url="https://openrouter.ai/api/v1",
                       model="openai/gpt-4o-mini"),
            client)

        result = session.complete_json(
            "system prompt", "recipe input",
            schema_name="recipe", json_schema={"type": "object"})

        self.assertEqual(result, '{"ok": 1}')
        client.chat.completions.create.assert_called_once_with(
            model="openai/gpt-4o-mini",
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "recipe input"},
            ],
            response_format={"type": "json_schema", "json_schema": {
                "name": "recipe", "strict": True, "schema": {"type": "object"},
            }},
        )

    def test_anthropic_reads_json_back_out_of_a_forced_tool_call(self):
        """Claude has no JSON-schema response format; a forced tool is the way."""
        client = Mock()
        client.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type="tool_use", input={"ok": 1}),
        ])
        session = self._session(
            AiProvider(id="c", name="Claude", api_type=ANTHROPIC,
                       api_key="k", model="claude-sonnet-4-5"),
            client)

        result = session.complete_json(
            "sys", "user", schema_name="recipe", json_schema={"type": "object"})

        self.assertEqual(json.loads(result), {"ok": 1})
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["system"], "sys")
        self.assertEqual(kwargs["tool_choice"], {"type": "tool", "name": "recipe"})
        self.assertEqual(kwargs["tools"][0]["input_schema"], {"type": "object"})

    def test_anthropic_falls_back_to_text_when_no_tool_was_called(self):
        client = Mock()
        client.messages.create.return_value = SimpleNamespace(content=[
            SimpleNamespace(type="text", text='```json\n{"ok": 2}\n```'),
        ])
        session = self._session(
            AiProvider(id="c", name="Claude", api_type=ANTHROPIC,
                       api_key="k", model="claude-sonnet-4-5"),
            client)
        self.assertEqual(
            json.loads(session.complete_json("sys", "user")), {"ok": 2})

    def test_only_gemini_can_be_handed_a_whole_video(self):
        gemini = AiSession(AiProvider(id="g", name="G", api_type=GEMINI,
                                      api_key="k", model="gemini-2.5-flash"))
        openai = AiSession(AiProvider(id="o", name="O", api_type=OPENAI,
                                      api_key="k", model="gpt-x"))
        self.assertTrue(gemini.supports_video_upload)
        self.assertFalse(openai.supports_video_upload)
        with self.assertRaises(ProviderConfigError):
            openai.complete_json_with_video("prompt", "/tmp/nope.mp4")


class ModelFallbackTests(unittest.TestCase):
    def test_a_working_model_is_written_back_to_its_own_provider(self):
        """Self-healing has to survive several providers sharing a dialect."""
        providers = [
            AiProvider(id="one", name="One", api_type=OPENAI, api_key="k",
                       model="gpt-retired"),
            AiProvider(id="two", name="Two", api_type=OPENAI, api_key="k",
                       model="gpt-untouched"),
        ]
        written = {}

        def _record(key, value):
            written[key] = value
            return True

        client = Mock()
        client.responses.create.side_effect = [
            Exception("404 model_not_found"),
            SimpleNamespace(output_text='{"ok": 1}'),
        ]

        session = AiSession(providers[0])
        session._client = client
        with patch.object(config, "_db_config",
                          {"ai_providers": serialize_providers(providers)}), \
             patch("config.set_config_value", _record), \
             patch.object(config, "reload", lambda: None):
            session.complete_json("sys", "user")

        saved = {p["id"]: p["model"] for p in json.loads(written["ai_providers"])}
        self.assertEqual(saved["one"], session.model)
        self.assertNotEqual(saved["one"], "gpt-retired")
        self.assertEqual(saved["two"], "gpt-untouched")


class ChefUsesTheConfiguredProviderTests(unittest.TestCase):
    def test_chef_speaks_whatever_dialect_the_provider_uses(self):
        from chef import Chef

        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])
        registry = serialize_providers([
            AiProvider(id="gw", name="Gateway", api_type=OPENAI_COMPATIBLE,
                       api_key="k", base_url="https://gateway.test/v1",
                       model="some/model"),
        ])

        with patch.object(config, "_db_config", {"ai_providers": registry}):
            chef = Chef(source_url="https://example.com/recipe",
                        description="", transcription="")
            chef.session._client = client
            result = chef._call_llm("system prompt", "recipe input",
                                    schema_name="recipe",
                                    json_schema={"type": "object"})

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(
            client.chat.completions.create.call_args.kwargs["model"], "some/model")


if __name__ == "__main__":
    unittest.main()
