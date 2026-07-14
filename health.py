"""
Startup / runtime health checks.

Surfaces the two most common production-failure classes early and with clear,
actionable messages instead of letting them blow up mid-extraction:

  1. yt-dlp upstream drift - the bundled yt-dlp falls behind platform changes
     (YouTube/TikTok/Instagram), so downloads start failing. We verify yt-dlp
     and its runtime deps (ffmpeg, deno) are present and report the version.
  2. LLM model drift - the configured provider model gets retired/deprecated
     and 404s (see PIC-34). We verify a provider + key is configured and, when
     possible, that the configured model still exists.

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
    """Check that an LLM provider + key + model is configured (and reachable).

    Args:
        probe_network: if True, verify the configured model actually exists by
            listing the provider's models. Off by default.
    """
    config.reload()
    provider = config.LLM_PROVIDER

    if provider == "openai":
        api_key = config.OPENAI_API_KEY
        model = config.OPENAI_MODEL
    elif provider == "gemini":
        api_key = config.GEMINI_API_KEY
        model = config.GEMINI_MODEL
    else:
        return _result(
            "llm", False,
            f"Unknown LLM provider configured: '{provider}'",
            "Set provider to 'openai' or 'gemini' in Settings.",
        )

    if not api_key:
        return _result(
            "llm", False,
            f"No API key configured for provider '{provider}'",
            f"Add your {provider} API key in Settings.",
        )

    fallbacks = candidate_models(provider, model)

    if probe_network:
        try:
            available = _list_available_models(provider, api_key)
        except Exception as exc:
            return _result(
                "llm", False,
                f"Could not reach {provider} to verify model '{model}': {exc}",
                f"Check the {provider} API key and network connectivity.",
            )
        # Provider model ids are sometimes namespaced (e.g. 'models/gemini-...').
        if available and not any(model in m or m in model for m in available):
            return _result(
                "llm", False,
                f"Configured {provider} model '{model}' is not in the provider's "
                f"available model list - it may be retired/deprecated.",
                f"Update the {provider} model in Settings. The app will fall back "
                f"to {fallbacks[1:]} automatically at call time, but you should "
                f"set a supported model.",
            )

    return _result(
        "llm", True,
        f"Provider '{provider}' configured with model '{model}' "
        f"(fallback chain: {fallbacks})",
    )


def _list_available_models(provider: str, api_key: str) -> list[str]:
    """Return available model ids for the provider (best-effort)."""
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        return [m.id for m in client.models.list().data]
    if provider == "gemini":
        from google import genai
        client = genai.Client(api_key=api_key)
        return [getattr(m, "name", "") for m in client.models.list()]
    return []


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
