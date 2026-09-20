"""Shared OpenRouter client helper.

OpenRouter (https://openrouter.ai) exposes an OpenAI-compatible **Chat
Completions** API at a custom base URL, so we reuse the official ``openai``
SDK pointed at that endpoint. Note this is deliberately the Chat Completions
surface (``client.chat.completions.create``) rather than the newer Responses
API that the native OpenAI provider uses - OpenRouter does not implement the
Responses API.

The API key is read from Settings (SQLite config), never hardcoded.
"""

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Optional attribution headers OpenRouter uses for its rankings/dashboards.
# Harmless if omitted; they carry no secrets.
_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://github.com/pickeld/pick-a-recipe",
    "X-Title": "Pick-a-Recipe",
}


def make_openrouter_client():
    """Return an ``openai.OpenAI`` client configured for OpenRouter.

    Imports the SDK lazily so environments without the optional dependency (or
    that never select OpenRouter) do not pay the import cost.
    """
    from openai import OpenAI
    from config import config

    return OpenAI(
        api_key=config.OPENROUTER_API_KEY or "not-configured",
        base_url=OPENROUTER_BASE_URL,
        default_headers=_DEFAULT_HEADERS,
    )
