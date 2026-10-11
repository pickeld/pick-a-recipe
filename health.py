"""
Startup / runtime health checks.

Surfaces the two most common production-failure classes early and with clear,
actionable messages instead of letting them blow up mid-extraction:

  1. yt-dlp upstream drift - the bundled yt-dlp falls behind platform changes
     (YouTube/TikTok/Instagram), so downloads start failing. We verify yt-dlp
     and its runtime deps (ffmpeg, deno) are present and report the version.
  2. LLM model drift - the configured provider model gets retired/deprecated
     and 404s (see PIC-34). We verify the provider chosen for extraction is
     usable and, when possible, that its model still exists.

Checks are intentionally non-fatal: they log actionable warnings and expose a
machine-readable result (used by /api/health and the container HEALTHCHECK) so
operators see the problem before a user hits it. The LLM resilience layer
(llm_resilience.py) handles automatic recovery at call time.
"""

import shutil
import subprocess

from config import config
from helpers import setup_logger
from llm_resilience import candidate_models

logger = setup_logger(__name__)


def _result(name, ok, detail, remediation=None):
    return {
        "name": name,
        "ok": ok,
        "detail": detail,
        "remediation": remediation,
    }


def check_ytdlp(probe_network: bool = False) -> dict:
    """Check that yt-dlp and its runtime dependencies are usable.

    Args:
        probe_network: if True, additionally attempt a lightweight metadata
            fetch against a stable public video to catch upstream drift that a
            version check alone can miss. Off by default (slow / network-bound).
    """
    try:
        import yt_dlp
    except Exception as exc:
        return _result(
            "yt-dlp", False,
            f"yt-dlp is not importable: {exc}",
            "Reinstall dependencies: pip install -r requirements.txt",
        )

    version = getattr(getattr(yt_dlp, "version", None), "__version__", "unknown")

    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return _result(
            "yt-dlp", False,
            f"yt-dlp {version} present but curl-cffi is missing (required for Instagram)",
            "Reinstall with browser impersonation support: "
            "pip install \"yt-dlp[curl-cffi]\". Docker users should pull the latest image.",
        )

    # ffmpeg is required for audio extraction / muxing; deno is required by
    # yt-dlp's YouTube extractor. Missing either fails downloads, not import.
    missing = [tool for tool in ("ffmpeg", "deno") if shutil.which(tool) is None]
    if missing:
        return _result(
            "yt-dlp", False,
            f"yt-dlp {version} present but required tool(s) missing: {', '.join(missing)}",
            "Install the missing tool(s). The Docker image installs ffmpeg and "
            "deno; if running outside Docker, install them on the host.",
        )

    if probe_network:
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                                   "skip_download": True}) as ydl:
                # Stable, long-lived public video used purely as a reachability
                # probe for the extractor pipeline.
                ydl.extract_info(
                    "https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
        except Exception as exc:
            return _result(
                "yt-dlp", False,
                f"yt-dlp {version} failed a live extraction probe: {exc}",
                "yt-dlp likely needs an upgrade to match platform changes: "
                "pip install --upgrade yt-dlp. For YouTube bot checks, configure "
                "cookies in Settings.",
            )

    return _result("yt-dlp", True, f"yt-dlp {version} OK (ffmpeg, deno, curl-cffi present)")


def check_llm(probe_network: bool = False) -> dict:
    """Check that an AI provider is configured for extraction (and reachable).

    Args:
        probe_network: if True, verify the configured model actually exists by
            listing the provider's models. Off by default.
    """
    from ai_providers import (
        ProviderConfigError, describe, selected_provider, validate,
    )

    config.reload()
    try:
        provider = selected_provider()
    except ProviderConfigError as exc:
        return _result(
            "llm", False, str(exc),
            "Add a provider under Settings -> AI Providers.",
        )

    try:
        validate(provider)
    except ProviderConfigError as exc:
        return _result(
            "llm", False, str(exc),
            "Fix this provider under Settings -> AI Providers.",
        )

    fallbacks = candidate_models(provider.api_type, provider.model)

    if probe_network:
        try:
            available = _list_available_models(provider)
        except Exception as exc:
            return _result(
                "llm", False,
                f"Could not reach {provider.label} to verify model "
                f"'{provider.model}': {exc}",
                f"Check the {provider.label} API key, base URL and network "
                f"connectivity.",
            )
        # Provider model ids are sometimes namespaced (e.g. 'models/gemini-...').
        if available and not any(
            provider.model in m or m in provider.model for m in available
        ):
            return _result(
                "llm", False,
                f"Configured model '{provider.model}' is not in "
                f"{provider.label}'s available model list - it may be "
                f"retired or not served there.",
                f"Update the model for {provider.label} in Settings."
                + (f" The app will fall back to {fallbacks[1:]} automatically "
                   f"at call time, but you should set a supported model."
                   if len(fallbacks) > 1 else ""),
            )

    chain = f" (fallback chain: {fallbacks})" if len(fallbacks) > 1 else ""
    return _result("llm", True, f"Extraction uses {describe(provider)}{chain}")


def _list_available_models(provider) -> list[str]:
    """Return available model ids for a provider (best-effort)."""
    from ai_providers import ANTHROPIC, GEMINI, AiSession

    client = AiSession(provider).client
    if provider.api_type == GEMINI:
        return [getattr(m, "name", "") for m in client.models.list()]
    if provider.api_type == ANTHROPIC:
        return [m.id for m in client.models.list().data]
    # Both OpenAI dialects expose GET /models.
    return [m.id for m in client.models.list().data]


def run_health_checks(probe_network: bool = False) -> dict:
    """Run all health checks and return an aggregate result."""
    checks = [check_ytdlp(probe_network=probe_network),
              check_llm(probe_network=probe_network)]
    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
    }


def run_startup_health_check(probe_network: bool = False) -> dict:
    """Run checks at startup and log clear, actionable results. Never raises."""
    logger.info("[Health] Running startup health checks...")
    try:
        report = run_health_checks(probe_network=probe_network)
    except Exception as exc:  # pragma: no cover - defensive, must not crash boot
        logger.error("[Health] Health check itself failed: %s", exc)
        return {"ok": False, "checks": [], "error": str(exc)}

    for check in report["checks"]:
        if check["ok"]:
            logger.info("[Health] OK   - %s: %s", check["name"], check["detail"])
        else:
            logger.error("[Health] FAIL - %s: %s", check["name"], check["detail"])
            if check["remediation"]:
                logger.error("[Health]        -> %s", check["remediation"])

    if report["ok"]:
        logger.info("[Health] All startup checks passed.")
    else:
        logger.warning(
            "[Health] One or more startup checks FAILED - see actionable messages "
            "above. The app will still start; affected features may error until fixed.")
    return report
