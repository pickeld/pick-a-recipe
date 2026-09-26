"""Model-name suggestions from models.dev.

Purely a convenience for the settings page: every model field takes a typed
value, so a gateway models.dev has never heard of, or a release from this
morning, is still perfectly usable. The catalog only saves people from having
to remember exact model ids.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from helpers import setup_logger

logger = setup_logger(__name__)

_CATALOG_URL = "https://models.dev/api.json"
_TIMEOUT = 15
# The document is several megabytes and changes when a vendor ships a model,
# so a long cache costs nothing and keeps the settings page snappy.
_TTL_SECONDS = 6 * 60 * 60

_lock = threading.Lock()
_cached: dict[str, Any] | None = None
_cached_at = 0.0


def _fetch() -> dict[str, Any]:
    from helpers import create_http_session
    from url_safety import safe_get

    resp = safe_get(create_http_session(), _CATALOG_URL, timeout=_TIMEOUT)
    resp.raise_for_status()
    document = resp.json()
    if not isinstance(document, dict):
        raise ValueError("models.dev returned an unexpected document")
    return document


def load_catalog(force: bool = False) -> dict[str, Any]:
    """The whole models.dev document, cached in memory."""
    global _cached, _cached_at
    with _lock:
        fresh = _cached is not None and (time.monotonic() - _cached_at) < _TTL_SECONDS
        if fresh and not force:
            return _cached  # type: ignore[return-value]
    document = _fetch()
    with _lock:
        _cached = document
        _cached_at = time.monotonic()
    return document


def catalog_models(catalog: str) -> list[dict[str, str]]:
    """Suggestable models for one models.dev provider, best candidates first.

    Only models that can both take an image and be held to a JSON schema are
    offered: recipe extraction reads video frames and needs structured output,
    so a text-only or unstructured model would be a suggestion that cannot do
    the job.
    """
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
        suggestions.append({
            "id": str(model_id),
            "name": str(model.get("name") or model_id),
            "released": str(model.get("release_date") or ""),
        })

    suggestions.sort(key=lambda m: (m["released"], m["id"]), reverse=True)
    return suggestions
