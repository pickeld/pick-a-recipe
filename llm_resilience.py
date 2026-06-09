"""
LLM resilience layer.

Wraps LLM calls so that a configured model which has been retired/deprecated
(returning a 404 / "model not found") no longer takes the whole app down.
Instead we transparently fall back to the next known-good model for the
provider, persist the working model back to config (self-healing), and only
raise once every candidate has been exhausted - with a clear, actionable error.

This is the core fix for the most common production-failure class (see PIC-34,
where a hardcoded gemini-2.0-flash 404'd after Google retired it).
"""

from helpers import setup_logger

logger = setup_logger(__name__)


# Per-provider fallback chains, most-preferred first. These are deliberately
# conservative lists of widely-available, vision + responses-capable models.
# The model the user actually configured is always tried FIRST (see
# candidate_models); these are only used when that model is unavailable.
FALLBACK_MODELS = {
    "openai": ["gpt-5-mini-2025-08-07", "gpt-4o-mini", "gpt-4o"],
    "gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
}


# Substrings that indicate the requested model is gone/unusable rather than a
# transient failure. Matched case-insensitively against str(exc).
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


class ModelUnavailableError(RuntimeError):
    """Raised when a model and all its fallbacks are unavailable."""


def is_model_unavailable_error(exc: BaseException) -> bool:
    """Return True if the exception looks like "this model is gone/unusable".

    Provider-agnostic: inspects HTTP status (404) and the error message rather
    than importing provider SDK exception types, so it works regardless of
    which provider raised and without those SDKs installed.
    """
    # HTTP 404 from either SDK is a strong signal the model id is invalid.
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status == 404 or status == "404":
        return True

    message = str(exc).lower()
    # A 404 embedded in the message (genai often raises ClientError with the
    # status code in the text rather than as an attribute).
    if "404" in message and ("model" in message or "not found" in message):
        return True

    return any(marker in message for marker in _MODEL_GONE_MARKERS)


def candidate_models(provider: str, configured_model: str) -> list[str]:
    """Ordered, de-duplicated list of models to try for a provider.

    The configured model comes first (so we honour the user's choice), followed
    by the provider's known-good fallbacks.
    """
    ordered: list[str] = []
    for model in [configured_model, *FALLBACK_MODELS.get(provider, [])]:
        if model and model not in ordered:
            ordered.append(model)
    return ordered


def _persist_working_model(provider: str, model: str) -> None:
    """Best-effort: remember the model that worked so we stop hitting the 404.

    Mirrors what PIC-34 had to do by hand (rewrite the retired model id), but
    automatically and only after we've proven the new model works.
    """
    try:
        from config import config, set_config_value

        key = f"{provider}_model"
        set_config_value(key, model)
        config.reload()
        logger.info(
            "Persisted working %s model '%s' to config (was failing over from a "
            "previously configured model).",
            provider, model,
        )
    except Exception as exc:  # pragma: no cover - persistence is best-effort
        logger.warning("Could not persist working model '%s': %s", model, exc)


def call_with_model_fallback(provider, configured_model, call, *, persist=True):
    """Run ``call(model)`` against each candidate model until one succeeds.

    Args:
        provider: "openai" or "gemini".
        configured_model: the model the user configured (tried first).
        call: callable taking a model name and returning the provider response.
        persist: if True, persist the working model when it differs from the
            configured one, so future calls skip the dead model.

    Returns:
        ``(result, used_model)`` - the value returned by ``call`` and the model
        that produced it.

    Raises:
        ModelUnavailableError: if every candidate model is unavailable.
        Exception: any non-"model unavailable" error is re-raised immediately
            (we only fail over on deprecation/404, not on real runtime errors).
    """
    candidates = candidate_models(provider, configured_model)
    last_error: BaseException | None = None

    for index, model in enumerate(candidates):
        try:
            result = call(model)
        except Exception as exc:
            if not is_model_unavailable_error(exc):
                # A genuine error (bad key, network, content) - don't mask it
                # by churning through models.
                raise
            last_error = exc
            remaining = candidates[index + 1:]
            logger.warning(
                "%s model '%s' is unavailable (%s). %s",
                provider, model, exc,
                f"Falling back to '{remaining[0]}'." if remaining
                else "No fallback models left.",
            )
            continue

        # Success. If we had to fail over, remember the working model.
        if persist and model != configured_model:
            _persist_working_model(provider, model)
        return result, model

    raise ModelUnavailableError(
        f"The configured {provider} model '{configured_model}' is unavailable "
        f"(likely retired/deprecated) and all fallbacks were also unavailable: "
        f"{candidates}. Update the {provider} model in Settings to a currently "
        f"supported model. Last error: {last_error}"
    )
