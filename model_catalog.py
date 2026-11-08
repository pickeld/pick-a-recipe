"""Model-name suggestions, with prices, for the settings page.

Mostly from models.dev. OpenRouter is asked directly instead: it lists every
model it routes to with its own current price, which models.dev only mirrors.

Purely a convenience: every model field takes a typed value, so a gateway
neither source has heard of, or a release from this morning, is still
perfectly usable. The catalog only saves people from having to remember exact
model ids and look up what they cost.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from helpers import setup_logger

logger = setup_logger(__name__)

_CATALOG_URL = "https://models.dev/api.json"
_OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT = 15
# The documents are large and change when a vendor ships a model or reprices
# one, so a long cache costs nothing and keeps the settings page snappy.
_TTL_SECONDS = 6 * 60 * 60

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _fetch(url: str) -> Any:
    from helpers import create_http_session
    from url_safety import safe_get

    resp = safe_get(create_http_session(), url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _cached(url: str, force: bool = False) -> Any:
    with _lock:
        hit = _cache.get(url)
        if hit and not force and (time.monotonic() - hit[0]) < _TTL_SECONDS:
            return hit[1]
    document = _fetch(url)
    with _lock:
        _cache[url] = (time.monotonic(), document)
    return document


def load_catalog(force: bool = False) -> dict[str, Any]:
    """The whole models.dev document, cached in memory."""
    document = _cached(_CATALOG_URL, force)
    if not isinstance(document, dict):
        raise ValueError("models.dev returned an unexpected document")
    return document


def _usd(value: Any, scale: float = 1.0) -> float | None:
    """A price in USD per million tokens, or None when unknown or variable."""
    try:
        price = float(value) * scale
    except (TypeError, ValueError):
        return None
    # OpenRouter prices its auto-router as -1: it depends on where it routes.
    return round(price, 4) if price >= 0 else None


def openrouter_models() -> list[dict[str, Any]]:
    """OpenRouter's live model list, newest first.

    Same bar as catalog_models(): the model must take images and support a
    strict JSON schema, because that is what extraction sends.
    """
    document = _cached(_OPENROUTER_URL)
    models = document.get("data") if isinstance(document, dict) else None
    if not isinstance(models, list):
        raise ValueError("OpenRouter returned an unexpected document")

    suggestions = []
    for model in models:
        if not isinstance(model, dict) or not model.get("id"):
            continue
        inputs = (model.get("architecture") or {}).get("input_modalities") or []
        if "image" not in inputs or "structured_outputs" not in (model.get("supported_parameters") or []):
            continue
        pricing = model.get("pricing") or {}
        created = model.get("created")
        released = (
            datetime.fromtimestamp(created, tz=timezone.utc).date().isoformat()
            if isinstance(created, (int, float)) else ""
        )
        suggestions.append({
            "id": str(model["id"]),
            "name": str(model.get("name") or model["id"]),
            "released": released,
            # OpenRouter quotes USD per token.
            "input_cost": _usd(pricing.get("prompt"), 1_000_000),
            "output_cost": _usd(pricing.get("completion"), 1_000_000),
            "context": model.get("context_length") if isinstance(model.get("context_length"), int) else None,
        })

    suggestions.sort(key=lambda m: (m["released"], m["id"]), reverse=True)
    return suggestions


def catalog_models(catalog: str) -> list[dict[str, Any]]:
    """Suggestable models for one provider catalog, best candidates first.

    Only models that can both take an image and be held to a JSON schema are
    offered: recipe extraction reads video frames and needs structured output,
    so a text-only or unstructured model would be a suggestion that cannot do
    the job.
    """
    if catalog == "openrouter":
        try:
            return openrouter_models()
        except Exception as exc:
            logger.info("[AI] OpenRouter model list unavailable, using models.dev: %s", exc)

    provider = (load_catalog().get(catalog) or {})
    models = provider.get("models") or {}
    if not isinstance(models, dict):
        return []

    suggestions = []
    for model_id, model in models.items():
        if not isinstance(model, dict):
            continue
        modalities = model.get("modalities") or {}
        inputs = modalities.get("input") or []
        if "image" not in inputs or not model.get("structured_output"):
            continue
        cost = model.get("cost") or {}
        context = (model.get("limit") or {}).get("context")
        suggestions.append({
            "id": str(model_id),
            "name": str(model.get("name") or model_id),
            "released": str(model.get("release_date") or ""),
            # models.dev already quotes USD per million tokens.
            "input_cost": _usd(cost.get("input")),
            "output_cost": _usd(cost.get("output")),
            "context": context if isinstance(context, int) else None,
        })

    suggestions.sort(key=lambda m: (m["released"], m["id"]), reverse=True)
    return suggestions
