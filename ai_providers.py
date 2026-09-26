"""User-configured AI providers.

One place that knows how to reach a model, so the rest of the app asks for
"the provider configured for recipe extraction" instead of branching on a
hardcoded vendor list. A provider is an API dialect, a key, a model name and
optionally a base URL — which between them reach OpenAI, Gemini, Anthropic,
OpenRouter, Groq, Together, a local Ollama or anything else speaking one of
those dialects.

The registry lives in the SQLite config table as JSON under ``ai_providers``.
Installs predating it derive an equivalent registry from the old
``llm_provider`` / ``openai_*`` / ``gemini_*`` / ``openrouter_*`` settings, so
nothing has to be reconfigured on upgrade.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Callable

from helpers import setup_logger
from llm_resilience import call_with_model_fallback
from recipe_schema import extract_json, to_gemini_json_schema

logger = setup_logger(__name__)

# The API dialects a provider can speak. Anything not on this list cannot be
# saved, so a typo in the database cannot turn into an obscure runtime failure.
OPENAI = "openai"
OPENAI_COMPATIBLE = "openai-compatible"
GEMINI = "gemini"
ANTHROPIC = "anthropic"

API_TYPES: dict[str, dict[str, Any]] = {
    OPENAI: {
        "label": "OpenAI (Responses API)",
        "needs_base_url": False,
        "default_base_url": "",
        # models.dev provider id used to suggest model names in Settings.
        "catalog": "openai",
        "example_model": "gpt-5-mini-2025-08-07",
    },
    OPENAI_COMPATIBLE: {
        "label": "OpenAI-compatible (Chat Completions)",
        "needs_base_url": True,
        "default_base_url": "https://openrouter.ai/api/v1",
        "catalog": "",  # inferred from the base URL; see catalog_for()
        "example_model": "openai/gpt-4o-mini",
    },
    GEMINI: {
        "label": "Google Gemini",
        "needs_base_url": False,
        "default_base_url": "",
        "catalog": "google",
        "example_model": "gemini-2.5-flash",
    },
    ANTHROPIC: {
        "label": "Anthropic (Claude)",
        "needs_base_url": False,
        "default_base_url": "",
        "catalog": "anthropic",
        "example_model": "claude-sonnet-4-5",
    },
}

# Hosts whose OpenAI-compatible endpoints we recognise, so Settings can suggest
# their model names. Purely a convenience: an unrecognised host still works,
# the model name is just typed by hand.
_COMPATIBLE_CATALOGS = {
    "openrouter.ai": "openrouter",
    "api.groq.com": "groq",
    "api.deepseek.com": "deepseek",
    "api.mistral.ai": "mistral",
    "api.x.ai": "xai",
    "api.together.xyz": "togetherai",
    "api.cerebras.ai": "cerebras",
}

# Attribution headers OpenRouter uses for its dashboards. Harmless elsewhere,
# and they carry no secrets.
_OPENAI_COMPATIBLE_HEADERS = {
    "HTTP-Referer": "https://github.com/pickeld/pick-a-recipe",
    "X-Title": "Pick-a-Recipe",
}

_MAX_PROVIDERS = 20
_ID_RE = re.compile(r"[^a-z0-9]+")


class ProviderConfigError(ValueError):
    """A provider record is not usable as configured."""


@dataclass(frozen=True)
class AiProvider:
    """One configured way to reach a model."""

    id: str
    name: str
    api_type: str
    api_key: str = ""
    base_url: str = ""
    model: str = ""

    @property
    def label(self) -> str:
        return self.name or self.id

    def catalog(self) -> str:
        """models.dev provider id whose models to suggest, or '' for none."""
        spec = API_TYPES.get(self.api_type) or {}
        if spec.get("catalog"):
            return str(spec["catalog"])
        return catalog_for_base_url(self.base_url)


def catalog_for_base_url(base_url: str) -> str:
    """The models.dev provider behind an OpenAI-compatible base URL, if known."""
    from urllib.parse import urlparse

    host = (urlparse(base_url or "").hostname or "").lower()
    return _COMPATIBLE_CATALOGS.get(host, "")


def slugify(name: str) -> str:
    """A stable, readable id for a provider named by the user."""
    slug = _ID_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug[:48] or f"provider-{uuid.uuid4().hex[:8]}"


# ===== Registry =====

def _coerce(record: Any) -> AiProvider | None:
    """One stored record as an AiProvider, or None when it is unusable."""
    if not isinstance(record, dict):
        return None
    api_type = str(record.get("api_type") or "").strip()
    if api_type not in API_TYPES:
        return None
    name = str(record.get("name") or "").strip()
    provider_id = str(record.get("id") or "").strip() or slugify(name)
    return AiProvider(
        id=provider_id,
        name=name or provider_id,
        api_type=api_type,
        api_key=str(record.get("api_key") or "").strip(),
        base_url=str(record.get("base_url") or "").strip().rstrip("/"),
        model=str(record.get("model") or "").strip(),
    )


def parse_providers(raw: str) -> list[AiProvider]:
    """Providers from the stored JSON, skipping anything malformed.

    A single bad record must not take Settings down, so this drops what it
    cannot read rather than raising.
    """
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("[AI] Stored provider list is not valid JSON; ignoring it.")
        return []
    if not isinstance(decoded, list):
        return []

    providers: list[AiProvider] = []
    seen: set[str] = set()
    for record in decoded[:_MAX_PROVIDERS]:
        provider = _coerce(record)
        if provider is None or provider.id in seen:
            continue
        seen.add(provider.id)
        providers.append(provider)
    return providers


def serialize_providers(providers: list[AiProvider]) -> str:
    return json.dumps([asdict(p) for p in providers], ensure_ascii=False)


# The registry an install predating this feature gets, built from the settings
# it already has. Derived on read rather than written on import: a first run
# that never opens Settings should not silently rewrite its own config.
def _legacy_providers(cfg) -> list[AiProvider]:
    candidates = [
        AiProvider(
            id="openai", name="OpenAI", api_type=OPENAI,
            api_key=cfg.OPENAI_API_KEY, model=cfg.OPENAI_MODEL,
        ),
        AiProvider(
            id="gemini", name="Google Gemini", api_type=GEMINI,
            api_key=cfg.GEMINI_API_KEY, model=cfg.GEMINI_MODEL,
        ),
        AiProvider(
            id="openrouter", name="OpenRouter", api_type=OPENAI_COMPATIBLE,
            api_key=cfg.OPENROUTER_API_KEY,
            base_url="https://openrouter.ai/api/v1",
            model=cfg.OPENROUTER_MODEL,
        ),
    ]
    selected = (cfg.LLM_PROVIDER or "").strip().lower()
    # Keep the one in use even without a key, so Settings opens on the provider
    # the operator chose rather than on an empty list.
    return [p for p in candidates if p.api_key or p.id == selected]


def load_providers(cfg=None) -> list[AiProvider]:
    """Every configured provider, newest schema first, legacy settings second."""
    if cfg is None:
        from config import config as cfg  # noqa: PLW0127
    stored = parse_providers(cfg.AI_PROVIDERS)
    return stored if stored else _legacy_providers(cfg)


def selected_provider(cfg=None) -> AiProvider:
    """The provider configured for recipe extraction.

    Raises:
        ProviderConfigError: nothing is configured, or the selection points at
            a provider that no longer exists and there is no obvious stand-in.
    """
    if cfg is None:
        from config import config as cfg  # noqa: PLW0127
    providers = load_providers(cfg)
    if not providers:
        raise ProviderConfigError(
            "No AI provider is configured. Add one under Settings -> AI Providers."
        )

    wanted = (cfg.AI_EXTRACTION_PROVIDER or cfg.LLM_PROVIDER or "").strip()
    by_id = {p.id: p for p in providers}
    if wanted in by_id:
        return by_id[wanted]
    if wanted:
        logger.warning(
            "[AI] Extraction provider %r is not configured; using %r instead.",
            wanted, providers[0].id,
        )
    return providers[0]


def describe(provider: AiProvider) -> str:
    """A one-line, secret-free summary for logs and health output."""
    spec = API_TYPES.get(provider.api_type) or {}
    where = f" at {provider.base_url}" if provider.base_url else ""
    return f"{provider.label} [{spec.get('label', provider.api_type)}]{where}, model {provider.model!r}"


def validate(provider: AiProvider, *, require_key: bool = True) -> None:
    """Raise ProviderConfigError if this provider cannot be used as configured.

    ``require_key`` off checks only what has to be right before a request can
    even be built. A missing key is left to fail as the provider's own 401,
    which says more than a guess made here would, and it keeps a half-configured
    instance constructible instead of erroring on import.
    """
    if provider.api_type not in API_TYPES:
        raise ProviderConfigError(f"Unknown API type {provider.api_type!r}.")
    if not provider.model:
        raise ProviderConfigError(
            f"Provider {provider.label!r} has no model name set.")
    if API_TYPES[provider.api_type]["needs_base_url"] and not provider.base_url:
        raise ProviderConfigError(
            f"Provider {provider.label!r} is OpenAI-compatible, so it needs a "
            f"base URL (for example https://openrouter.ai/api/v1)."
        )
    # A local OpenAI-compatible server (Ollama, LM Studio, vLLM) legitimately
    # has no key; the hosted APIs all require one.
    if require_key and not provider.api_key and provider.api_type != OPENAI_COMPATIBLE:
        raise ProviderConfigError(
            f"Provider {provider.label!r} has no API key set.")


# ===== Talking to a provider =====

def _image_data_url(path: str) -> str:
    with open(path, "rb") as handle:
        return "data:image/jpeg;base64," + base64.standard_b64encode(handle.read()).decode("utf-8")


def _image_b64(path: str) -> str:
    with open(path, "rb") as handle:
        return base64.standard_b64encode(handle.read()).decode("utf-8")


class AiSession:
    """One configured provider plus the model actually being used.

    ``model`` is mutable on purpose: after a fallback the session sticks with
    the model that worked rather than re-trying the dead one on every call.
    """

    def __init__(self, provider: AiProvider | None = None, model: str | None = None):
        self.provider = provider if provider is not None else selected_provider()
        validate(self.provider, require_key=False)
        self.model = model or self.provider.model
        self._client: Any = None
        logger.info("[AI] Using %s", describe(self.provider))

    # -- client ------------------------------------------------------------
    @property
    def api_type(self) -> str:
        return self.provider.api_type

    @property
    def supports_video_upload(self) -> bool:
        """Whether whole videos can be handed over instead of sampled frames."""
        return self.api_type == GEMINI

    @property
    def client(self) -> Any:
        """The vendor SDK client, built on first use.

        Lazily, so importing this module does not require every SDK to be
        installed - only the one the configured provider actually needs.
        """
        if self._client is not None:
            return self._client
        provider = self.provider
        if provider.api_type == OPENAI:
            from openai import OpenAI
            kwargs: dict[str, Any] = {"api_key": provider.api_key or "not-configured"}
            if provider.base_url:
                kwargs["base_url"] = provider.base_url
            self._client = OpenAI(**kwargs)
        elif provider.api_type == OPENAI_COMPATIBLE:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=provider.api_key or "not-configured",
                base_url=provider.base_url,
                default_headers=dict(_OPENAI_COMPATIBLE_HEADERS),
            )
        elif provider.api_type == GEMINI:
            from google import genai
            self._client = genai.Client(api_key=provider.api_key)
        elif provider.api_type == ANTHROPIC:
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise ProviderConfigError(
                    "The 'anthropic' package is not installed, so an Anthropic "
                    "provider cannot be used. Install it, or point this provider "
                    "at an OpenAI-compatible gateway instead."
                ) from exc
            self._client = Anthropic(
                api_key=provider.api_key,
                **({"base_url": provider.base_url} if provider.base_url else {}),
            )
        else:  # pragma: no cover - validate() rejects this first
            raise ProviderConfigError(f"Unknown API type {provider.api_type!r}.")
        return self._client

    # -- calls -------------------------------------------------------------
    def _run(self, call: Callable[[str], str]) -> str:
        """Run ``call`` against the model, falling back if it has been retired."""
        result, used_model = call_with_model_fallback(
            self.api_type, self.model, call,
            persist_model=self._remember_model,
            provider_label=self.provider.label,
        )
        self.model = used_model
        return result

    def _remember_model(self, model: str) -> None:
        """Write a model that worked back into this provider's record."""
        try:
            from config import config, set_config_value
            providers = load_providers(config)
            updated = [
                AiProvider(**{**asdict(p), "model": model}) if p.id == self.provider.id else p
                for p in providers
            ]
            set_config_value("ai_providers", serialize_providers(updated))
            config.reload()
        except Exception as exc:
            logger.warning("[AI] Could not persist working model %r: %s", model, exc)

    def complete_json(
        self,
        system_prompt: str,
        user_content: str,
        *,
        schema_name: str | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_model: type | None = None,
    ) -> str:
        """A text prompt answered as JSON conforming to the given schema."""
        return self._run(lambda model: _TEXT_CALLS[self.api_type](
            self, model, system_prompt, user_content,
            schema_name, json_schema, schema_model,
        ))

    def complete_json_with_images(
        self,
        prompt: str,
        image_paths: list[str],
        *,
        schema_name: str | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_model: type | None = None,
        detail: str = "low",
        label_images: bool = False,
    ) -> str:
        """A vision prompt over local JPEG files, answered as JSON.

        ``label_images`` interleaves "[Image N]" markers, which is what frame
        selection needs to be able to name the frame it picked.
        """
        return self._run(lambda model: _IMAGE_CALLS[self.api_type](
            self, model, prompt, image_paths, schema_name, json_schema,
            schema_model, detail, label_images,
        ))

    def complete_json_with_video(
        self,
        prompt: str,
        video_path: str,
        *,
        schema_name: str | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_model: type | None = None,
    ) -> str:
        """Hand a whole video over. Only providers with ``supports_video_upload``."""
        if not self.supports_video_upload:
            raise ProviderConfigError(
                f"{self.provider.label} cannot read a video directly; "
                f"extract frames instead."
            )
        return self._run(lambda model: _gemini_video(
            self, model, prompt, video_path, schema_model))


# -- per-dialect implementations ------------------------------------------
# Kept as module-level functions rather than AiSession methods so each dialect
# reads as one self-contained thing, and adding a fifth is a local change.

def _openai_text(session, model, system_prompt, user_content,
                 schema_name, json_schema, schema_model) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if json_schema is not None and schema_name:
        kwargs["text"] = {"format": {
            "type": "json_schema", "name": schema_name,
            "strict": True, "schema": json_schema,
        }}
    return session.client.responses.create(**kwargs).output_text or ""


def _openai_images(session, model, prompt, image_paths, schema_name,
                   json_schema, schema_model, detail, label_images) -> str:
    content: list[dict] = [{"type": "input_text", "text": prompt}]
    for index, path in enumerate(image_paths):
        content.append({
            "type": "input_image",
            "image_url": _image_data_url(path),
            "detail": detail,
        })
        if label_images:
            content.append({"type": "input_text", "text": f"[Image {index}]"})

    kwargs: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
    }
    if json_schema is not None and schema_name:
        kwargs["text"] = {"format": {
            "type": "json_schema", "name": schema_name,
            "strict": True, "schema": json_schema,
        }}
    return session.client.responses.create(**kwargs).output_text or ""


def _chat_messages(prompt, image_paths, detail, label_images) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": prompt}]
    for index, path in enumerate(image_paths):
        content.append({
            "type": "image_url",
            "image_url": {"url": _image_data_url(path), "detail": detail},
        })
        if label_images:
            content.append({"type": "text", "text": f"[Image {index}]"})
    return [{"role": "user", "content": content}]


def _compatible_text(session, model, system_prompt, user_content,
                     schema_name, json_schema, schema_model) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if json_schema is not None and schema_name:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema_name, "strict": True, "schema": json_schema,
        }}
    resp = session.client.chat.completions.create(**kwargs)
    return extract_json(resp.choices[0].message.content or "")


def _compatible_images(session, model, prompt, image_paths, schema_name,
                       json_schema, schema_model, detail, label_images) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": _chat_messages(prompt, image_paths, detail, label_images),
    }
    if json_schema is not None and schema_name:
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {
            "name": schema_name, "strict": True, "schema": json_schema,
        }}
    resp = session.client.chat.completions.create(**kwargs)
    return extract_json(resp.choices[0].message.content or "")


def _gemini_config(schema_model):
    from google.genai import types

    if schema_model is None:
        return None
    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=to_gemini_json_schema(schema_model),
    )


def _gemini_text(session, model, system_prompt, user_content,
                 schema_name, json_schema, schema_model) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "contents": f"{system_prompt}\n\n{user_content}",
    }
    generate_config = _gemini_config(schema_model)
    if generate_config is not None:
        kwargs["config"] = generate_config
    resp = session.client.models.generate_content(**kwargs)
    return extract_json(resp.text or "")


def _gemini_images(session, model, prompt, image_paths, schema_name,
                   json_schema, schema_model, detail, label_images) -> str:
    from google.genai import types

    parts = []
    for index, path in enumerate(image_paths):
        with open(path, "rb") as handle:
            parts.append(types.Part.from_bytes(data=handle.read(), mime_type="image/jpeg"))
        if label_images:
            parts.append(types.Part.from_text(text=f"[Image {index}]"))
    parts.append(types.Part.from_text(text=prompt))

    kwargs: dict[str, Any] = {
        "model": model,
        "contents": [types.Content(role="user", parts=parts)],
    }
    generate_config = _gemini_config(schema_model)
    if generate_config is not None:
        kwargs["config"] = generate_config
    resp = session.client.models.generate_content(**kwargs)
    return extract_json(resp.text or "")


def _gemini_video(session, model, prompt, video_path, schema_model) -> str:
    import time

    from google.genai import types

    client = session.client
    video_file = client.files.upload(file=video_path)
    while video_file.state and video_file.state.name == "PROCESSING":
        time.sleep(2)
        video_file = client.files.get(name=video_file.name or "")
    if video_file.state and video_file.state.name == "FAILED":
        raise RuntimeError(f"Video processing failed: {video_file.state}")

    kwargs: dict[str, Any] = {
        "model": model,
        "contents": [types.Content(role="user", parts=[
            types.Part.from_uri(
                file_uri=video_file.uri or "",
                mime_type=video_file.mime_type or "video/mp4",
            ),
            types.Part.from_text(text=prompt),
        ])],
    }
    generate_config = _gemini_config(schema_model)
    if generate_config is not None:
        kwargs["config"] = generate_config
    resp = client.models.generate_content(**kwargs)
    return extract_json(resp.text or "")


# Claude has no JSON-schema response format. A tool whose input schema is the
# one we want, forced with tool_choice, is the documented way to get JSON that
# conforms: the model fills in the tool arguments and we read them back.
_ANTHROPIC_MAX_TOKENS = 8192


def _anthropic_call(session, model, system_prompt, blocks,
                    schema_name, json_schema) -> str:
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": _ANTHROPIC_MAX_TOKENS,
        "messages": [{"role": "user", "content": blocks}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    if json_schema is not None and schema_name:
        kwargs["tools"] = [{
            "name": schema_name,
            "description": "Return the result in this structure.",
            "input_schema": json_schema,
        }]
        kwargs["tool_choice"] = {"type": "tool", "name": schema_name}

    resp = session.client.messages.create(**kwargs)
    for block in resp.content or []:
        if getattr(block, "type", "") == "tool_use":
            return json.dumps(block.input, ensure_ascii=False)
    texts = [getattr(b, "text", "") for b in (resp.content or [])
             if getattr(b, "type", "") == "text"]
    return extract_json("".join(texts))


def _anthropic_text(session, model, system_prompt, user_content,
                    schema_name, json_schema, schema_model) -> str:
    return _anthropic_call(
        session, model, system_prompt,
        [{"type": "text", "text": user_content}],
        schema_name, json_schema,
    )


def _anthropic_images(session, model, prompt, image_paths, schema_name,
                      json_schema, schema_model, detail, label_images) -> str:
    blocks: list[dict] = []
    for index, path in enumerate(image_paths):
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": _image_b64(path),
        }})
        if label_images:
            blocks.append({"type": "text", "text": f"[Image {index}]"})
    blocks.append({"type": "text", "text": prompt})
    return _anthropic_call(session, model, "", blocks, schema_name, json_schema)


_TEXT_CALLS: dict[str, Callable] = {
    OPENAI: _openai_text,
    OPENAI_COMPATIBLE: _compatible_text,
    GEMINI: _gemini_text,
    ANTHROPIC: _anthropic_text,
}

_IMAGE_CALLS: dict[str, Callable] = {
    OPENAI: _openai_images,
    OPENAI_COMPATIBLE: _compatible_images,
    GEMINI: _gemini_images,
    ANTHROPIC: _anthropic_images,
}


# ===== Speech to text =====
# Same registry, different capability: any provider speaking an OpenAI dialect
# exposes /audio/transcriptions, so a configured provider can transcribe instead
# of running Whisper on this machine.
_TRANSCRIBING_TYPES = frozenset({OPENAI, OPENAI_COMPATIBLE})


def transcription_provider(cfg=None) -> AiProvider | None:
    """The provider configured to transcribe audio, or None to run locally."""
    if cfg is None:
        from config import config as cfg  # noqa: PLW0127
    if cfg.TRANSCRIPTION_MODE != "provider":
        return None
    wanted = (cfg.TRANSCRIPTION_PROVIDER or "").strip()
    for provider in load_providers(cfg):
        if provider.id == wanted:
            return provider
    logger.warning(
        "[AI] Transcription provider %r is not configured; falling back to "
        "local Whisper.", wanted,
    )
    return None


def can_transcribe(provider: AiProvider) -> bool:
    """Whether this dialect exposes an audio-transcription endpoint."""
    return provider.api_type in _TRANSCRIBING_TYPES


def transcribe_audio(provider: AiProvider, audio_path: str, *,
                     model: str, language: str | None = None) -> str:
    """Transcribe a local audio file through a configured provider.

    Uses the OpenAI ``audio.transcriptions`` surface, which every
    OpenAI-compatible gateway implements at the same path.
    """
    if not can_transcribe(provider):
        raise ProviderConfigError(
            f"{provider.label} does not offer audio transcription. Pick a "
            f"provider with an OpenAI-compatible audio endpoint, or transcribe "
            f"locally."
        )
    session = AiSession(provider, model=model)
    with open(audio_path, "rb") as handle:
        kwargs: dict[str, Any] = {"model": model, "file": handle}
        if language:
            kwargs["language"] = language
        result = session.client.audio.transcriptions.create(**kwargs)
    # The SDK returns an object for the default response format and a plain
    # string when the gateway answers with text.
    return (getattr(result, "text", None) or str(result) or "").strip()
