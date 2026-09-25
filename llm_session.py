"""Provider-agnostic structured LLM calls.

Lifted out of Chef so anything else needing a JSON-schema-constrained call -
the Mealie optimizer - gets the same provider abstraction, model fallback and
self-healing config behaviour instead of a second copy of the plumbing.
"""

from typing import Any

from config import config
from helpers import setup_logger
from llm_resilience import call_with_model_fallback
from recipe_schema import extract_json, to_gemini_json_schema

logger = setup_logger(__name__)


class LlmSession:
    """One configured provider client plus the model actually being used.

    ``model`` is mutable on purpose: after a fallback the session sticks with
    the model that worked rather than re-trying the dead one on every call.
    """

    def __init__(self, model: str | None = None):
        self.provider = config.LLM_PROVIDER

        if self.provider == "openai":
            from openai import OpenAI
            self.client = OpenAI(api_key=config.OPENAI_API_KEY or "not-configured")
            self.model = model or config.OPENAI_MODEL
        elif self.provider == "gemini":
            from google import genai
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)
            self.model = model or config.GEMINI_MODEL
        elif self.provider == "openrouter":
            from llm_openrouter import make_openrouter_client
            self.client = make_openrouter_client()
            self.model = model or config.OPENROUTER_MODEL
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    def call(
        self,
        system_prompt: str,
        user_content: str,
        *,
        schema_name: str | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_model: type | None = None,
    ) -> str:
        """Call the LLM and return the response text.

        Wrapped with a model-fallback chain so a retired/deprecated model (404)
        transparently fails over to a known-good model instead of taking the
        whole call down (see PIC-34).

        When ``json_schema`` / ``schema_model`` are provided the provider is
        asked for structured JSON so we do not have to scrape markdown fences.
        """
        def _openai(model: str) -> str:
            kwargs: dict[str, Any] = {
                "model": model,
                "input": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            if json_schema is not None and schema_name:
                kwargs["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": json_schema,
                    }
                }
            resp = self.client.responses.create(**kwargs)
            return resp.output_text

        def _gemini(model: str) -> str:
            from google.genai import types

            kwargs: dict[str, Any] = {
                "model": model,
                "contents": f"{system_prompt}\n\n{user_content}",
            }
            if schema_model is not None:
                kwargs["config"] = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=to_gemini_json_schema(schema_model),
                )
            resp = self.client.models.generate_content(**kwargs)
            raw_text = resp.text or ""
            logger.debug(f"Gemini raw response: {raw_text[:500]}...")
            return extract_json(raw_text)

        def _openrouter(model: str) -> str:
            # OpenRouter speaks the OpenAI Chat Completions dialect, so the
            # structured-output request uses ``response_format`` (not the
            # Responses API ``text.format`` the native OpenAI path uses).
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            }
            if json_schema is not None and schema_name:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": json_schema,
                    },
                }
            resp = self.client.chat.completions.create(**kwargs)
            raw_text = resp.choices[0].message.content or ""
            logger.debug(f"OpenRouter raw response: {raw_text[:500]}...")
            return extract_json(raw_text)

        if self.provider == "openai":
            call = _openai
        elif self.provider == "gemini":
            call = _gemini
        elif self.provider == "openrouter":
            call = _openrouter
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

        result, used_model = call_with_model_fallback(self.provider, self.model, call)
        self.model = used_model
        return result
