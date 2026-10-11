"""
LLM resilience layer.

Wraps LLM calls so that a configured model which has been retired/deprecated
(returning a 404 / "model not found") no longer takes the whole app down.
Instead we transparently fall back to the next known-good model for the
provider, persist the working model back to config (self-healing), and only
raise once every candidate has been exhausted - with a clear, actionable error.

Also handles transient errors (503 overloaded, 429 rate-limit, network blips)
with exponential back-off retries. If retries are exhausted on a model, we
fall through to the next fallback model rather than failing the whole request.
"""

import time

from helpers import setup_logger

logger = setup_logger(__name__)


# Fallback chains per API dialect, most-preferred first. Keyed by dialect and
# not by configured provider: the model names have to be ones the endpoint will
# actually recognise, and only the first-party APIs have a knowable set.
#
# "openai-compatible" deliberately has none. Behind that dialect could be
# OpenRouter, Groq, Together or a local Ollama, and a name that works on one is
# a 404 on the next - so a failed model falls through to a clear error instead
# of a second, equally wrong guess.
FALLBACK_MODELS = {
    "openai": ["gpt-5-mini-2025-08-07", "gpt-4o-mini", "gpt-4o"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
    "anthropic": ["claude-sonnet-4-5", "claude-haiku-4-5"],
    "openai-compatible": [],
}

_MODEL_GONE_MARKERS = (
    "model_not_found",
    "is not found",
    "not found",
    "no longer available",
    "does not exist",
    "deprecated",
    "has been deprecated",
    "is not supported",
    "not supported for",
    "unknown model",
    "invalid model",
)

_TRANSIENT_MARKERS = (
    "503",
    "overloaded",
    "high demand",
    "rate limit",
    "rate_limit",
    "resource_exhausted",
    "quota",
    "too many requests",
    "429",
    "502",
    "504",
    "service unavailable",
    "temporarily unavailable",
    "try again",
)

# Retry config for transient errors: up to 3 attempts, 5s / 15s back-off.
_TRANSIENT_MAX_RETRIES = 3
_TRANSIENT_RETRY_DELAYS = [5, 15]  # seconds between attempt 1→2, 2→3


class _TransientExhausted(Exception):
    """Internal: transient retries exhausted on a specific model. Fall through."""


class ModelUnavailableError(RuntimeError):
    """Raised when a model and all its fallbacks are unavailable."""


def is_model_unavailable_error(exc: BaseException) -> bool:
    """Return True if the exception looks like 'this model is gone/unusable'."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 404 or status == "404":
        return True
    message = str(exc).lower()
    if "404" in message and ("model" in message or "not found" in message):
        return True
    return any(marker in message for marker in _MODEL_GONE_MARKERS)


def is_transient_error(exc: BaseException) -> bool:
    """Return True if the exception looks like a temporary server-side spike."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (429, 502, 503, 504, "429", "502", "503", "504"):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def candidate_models(provider: str, configured_model: str) -> list[str]:
    """Ordered, de-duplicated list of models to try for a provider."""
    ordered: list[str] = []
    for model in [configured_model, *FALLBACK_MODELS.get(provider, [])]:
        if model and model not in ordered:
            ordered.append(model)
    return ordered


def _call_with_transient_retry(call, model: str, provider: str):
    """Call call(model), retrying on transient errors with back-off.

    Raises:
        _TransientExhausted: all retries consumed by transient errors — caller
            should try the next fallback model.
        Exception: any non-transient error, re-raised immediately.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, _TRANSIENT_MAX_RETRIES + 1):
        try:
            return call(model)
        except Exception as exc:
            if not is_transient_error(exc):
                raise  # non-transient — propagate immediately
            last_exc = exc
            if attempt < _TRANSIENT_MAX_RETRIES:
                delay = _TRANSIENT_RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "%s model '%s' transient error on attempt %d/%d (%s). "
                    "Retrying in %ds…",
                    provider, model, attempt, _TRANSIENT_MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)
            else:
                logger.warning(
                    "%s model '%s' transient error persisted after %d attempts (%s). "
                    "Trying next fallback model…",
                    provider, model, _TRANSIENT_MAX_RETRIES, exc,
                )
    raise _TransientExhausted(str(last_exc)) from last_exc


def call_with_model_fallback(provider, configured_model, call, *, persist=True,
                             persist_model=None, provider_label=None):
    """Run ``call(model)`` against each candidate model until one succeeds.

    Failure handling per model:
    - Transient (503/429/overloaded): retry with back-off, then fall through to
      the next model if all retries are exhausted.
    - Model-gone (404/deprecated): skip to the next model immediately.
    - Any other error: re-raise immediately (bad API key, content policy, etc.).

    Args:
        provider: the API dialect, which selects the fallback chain.
        persist_model: called with the model that worked when it is not the one
            that was configured, so the caller can write it back to wherever it
            keeps its settings. Suppressed by ``persist=False``.
        provider_label: what to call this provider in the error, since several
            configured providers can share a dialect.

    Returns:
        ``(result, used_model)``

    Raises:
        ModelUnavailableError: every candidate failed (gone or persistently overloaded).
        Exception: a non-retryable, non-gone error from the first model that raised it.
    """
    label = provider_label or provider
    candidates = candidate_models(provider, configured_model)
    last_error: BaseException | None = None

    for index, model in enumerate(candidates):
        remaining = candidates[index + 1:]
        try:
            result = _call_with_transient_retry(call, model, provider)
        except _TransientExhausted as exc:
            # All retries on this model exhausted — try next fallback
            last_error = exc.__cause__ or exc
            logger.warning(
                "%s model '%s' persistently overloaded. %s",
                provider, model,
                f"Falling back to '{remaining[0]}'." if remaining
                else "No fallback models left.",
            )
            continue
        except Exception as exc:
            if not is_model_unavailable_error(exc):
                raise
            last_error = exc
            logger.warning(
                "%s model '%s' is unavailable (%s). %s",
                provider, model, exc,
                f"Falling back to '{remaining[0]}'." if remaining
                else "No fallback models left.",
            )
            continue

        if persist and persist_model is not None and model != configured_model:
            try:
                persist_model(model)
                logger.info(
                    "Persisted working %s model '%s' (was failing over from a "
                    "previously configured model).", label, model,
                )
            except Exception as exc:
                logger.warning("Could not persist working model '%s': %s", model, exc)
        return result, model

    if len(candidates) == 1:
        raise ModelUnavailableError(
            f"Model '{configured_model}' on provider '{label}' is unavailable or "
            f"persistently overloaded, and this provider has no fallback chain. "
            f"Pick a model '{label}' currently serves, in Settings. "
            f"Last error: {last_error}"
        )
    raise ModelUnavailableError(
        f"The configured model '{configured_model}' on provider '{label}' and all "
        f"fallbacks are unavailable or persistently overloaded: {candidates}. "
        f"Pick a currently available model in Settings. "
        f"Last error: {last_error}"
    )
