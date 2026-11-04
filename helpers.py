from config import config
import logging
import os
import re
import threading
from collections import deque

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ==============================================================================
# Logging Configuration
# ==============================================================================

# LOG_LEVEL in the environment. debug is what a bug report needs; info is the
# default. A change applies on the next start of the process.
LOG_LEVEL_NAMES = ('warning', 'info', 'debug')
_LEVEL_BY_NAME = {
    'warning': logging.WARNING,
    'info': logging.INFO,
    'debug': logging.DEBUG,
}

# Newest lines kept in the process, already redacted. The copy a reporter
# pastes comes from the container log, which is the same stream.
_LOG_RING_CAPACITY = 500

_LOG_FORMAT = logging.Formatter(
    '%(asctime)s - %(name)s - %(funcName)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)

# Assignment-style secrets: api_key=..., "token": "...", Authorization: Bearer.
_SECRET_ASSIGNMENT = re.compile(
    r'(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|'
    r'password|passwd|secret|authorization|cookie|vapid[_-]?private[_-]?key)'
    r'\b\s*[=:]\s*)([^\s,;]+)'
)
_BEARER = re.compile(r'(?i)\bBearer\s+\S+')
_URL_SECRET = re.compile(
    r'(?i)([?&](?:api[_-]?key|access[_-]?token|token|password|secret|key)=)[^&\s]+'
)
_WELL_KNOWN_SECRET = re.compile(
    r'\b(?:sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|'
    r'gh[pousr]_[A-Za-z0-9]{8,}|'
    r'eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)'
)

# Config values that are credentials. Hosts and model names stay visible:
# a bug report needs them, and they are not keys.
_SECRET_CONFIG_KEYS = (
    'openai_api_key',
    'gemini_api_key',
    'openrouter_api_key',
    'mealie_api_key',
    'tandoor_api_key',
    'hf_token',
    'vapid_private_key',
)

_level_override: str | None = None
_ring: '_RingHandler | None' = None
# Loggers this module configured. Level changes touch only these, so a
# library logger that already has a handler is left alone.
_managed_loggers: set[str] = set()


def normalize_log_level(raw: str | None) -> str:
    """Map LOG_LEVEL onto warning, info, or debug."""
    name = (raw or '').strip().lower()
    if name in ('debug', 'verbose'):
        return 'debug'
    if name in ('warning', 'warn', 'quiet'):
        return 'warning'
    return 'info'


def _active_level_name() -> str:
    if _level_override in _LEVEL_BY_NAME:
        return _level_override
    return normalize_log_level(os.environ.get('LOG_LEVEL'))


def _formatter() -> logging.Formatter:
    return _LOG_FORMAT


class _RingHandler(logging.Handler):
    """Keep the newest log lines so Settings can hand them to a bug report."""

    def __init__(self, capacity: int) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=capacity)
        self._lines_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._lines_lock:
            self._lines.append(line)

    def snapshot(self) -> list[str]:
        with self._lines_lock:
            return list(self._lines)

    def clear(self) -> None:
        with self._lines_lock:
            self._lines.clear()


def _ring_handler() -> _RingHandler:
    global _ring
    if _ring is None:
        _ring = _RingHandler(_LOG_RING_CAPACITY)
        _ring.setFormatter(_formatter())
        _ring.addFilter(_RedactFilter())
    return _ring


def _configured_secrets() -> list[str]:
    """Credential values currently stored for this instance.

    Read at format time so a key saved after startup is still stripped.
    Short values are skipped: replacing them would punch holes in ordinary words.
    The values are used only as search needles. They are never written to a log.
    """
    found: list[str] = []
    try:
        values = [getattr(config, key.upper(), '') for key in _SECRET_CONFIG_KEYS]
        raw_providers = config.AI_PROVIDERS
    except Exception:
        return found
    for value in values:
        if isinstance(value, str) and len(value) >= 8:
            found.append(value)
    if isinstance(raw_providers, str) and raw_providers:
        try:
            import json
            records = json.loads(raw_providers)
        except (TypeError, ValueError):
            records = []
        if isinstance(records, list):
            for record in records:
                if not isinstance(record, dict):
                    continue
                api_key = record.get('api_key') or ''
                if isinstance(api_key, str) and len(api_key) >= 8:
                    found.append(api_key)
    return found


def redact_text(text: str) -> str:
    """Remove credentials from a log line before it is stored or shown.

    The copied log is what people paste into a public GitHub issue, so a key
    that reached a log message must not survive into that text. No credential
    is written here; values come from the instance config at format time.
    """
    cleaned = text.replace('\r', ' ').replace('\n', ' ')
    for secret in sorted(set(_configured_secrets()), key=len, reverse=True):
        cleaned = cleaned.replace(secret, '[redacted]')
    cleaned = _BEARER.sub('Bearer [redacted]', cleaned)
    cleaned = _URL_SECRET.sub(r'\1[redacted]', cleaned)
    cleaned = _SECRET_ASSIGNMENT.sub(r'\1[redacted]', cleaned)
    cleaned = _WELL_KNOWN_SECRET.sub('[redacted]', cleaned)
    return cleaned


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        cleaned = redact_text(record.getMessage())
        record.msg = cleaned
        record.args = None
        record.message = cleaned
        return True


def _attach(logger: logging.Logger, level: int) -> None:
    _managed_loggers.add(logger.name)
    logger.setLevel(level)
    # Each app logger owns its handlers. Propagating would print twice once
    # a root handler exists, and library loggers stay on their own.
    logger.propagate = False
    stream = next(
        (handler for handler in logger.handlers
         if isinstance(handler, logging.StreamHandler)
         and not isinstance(handler, _RingHandler)),
        None,
    )
    if stream is None:
        stream = logging.StreamHandler()
        stream.setFormatter(_formatter())
        stream.addFilter(_RedactFilter())
        logger.addHandler(stream)
    ring = _ring_handler()
    if ring not in logger.handlers:
        logger.addHandler(ring)
    for handler in logger.handlers:
        handler.setLevel(level)


def setup_logger(name: str) -> logging.Logger:
    """Create and configure a logger with time, function name, and severity.

    The level follows the LOG_LEVEL environment variable. debug is what a bug
    report asks for; info is the default.
    """
    logger = logging.getLogger(name)
    _attach(logger, _LEVEL_BY_NAME[_active_level_name()])
    return logger


def apply_log_level(raw: str | None = None) -> str:
    """Apply LOG_LEVEL from the environment, or an explicit value in tests.

    Called once at startup. Returns the canonical name (warning, info, or debug).
    """
    global _level_override
    if raw is None:
        name = normalize_log_level(os.environ.get('LOG_LEVEL'))
    else:
        name = normalize_log_level(raw)
    _level_override = name
    level = _LEVEL_BY_NAME[name]
    for logger_name in list(_managed_loggers):
        _attach(logging.getLogger(logger_name), level)
    return name


def recent_log_lines() -> list[str]:
    """Newest retained log lines, already redacted by the handler filter."""
    return _ring_handler().snapshot()


def recent_log_text() -> str:
    """Text block for the clipboard and for a GitHub bug report."""
    lines = recent_log_lines()
    header = [
        'pick-a-recipe diagnostic log',
        f'log_level: {_active_level_name()}',
        f'lines: {len(lines)}',
        'Credentials are removed before this text is shown. Read it once before posting.',
        '---',
    ]
    return '\n'.join(header + lines)


def clear_recent_logs() -> None:
    """Drop the retained lines. Used by tests."""
    _ring_handler().clear()


# ==============================================================================
# HTTP Utilities
# ==============================================================================

def create_http_session() -> requests.Session:
    """Create a requests session with retry logic and proper timeouts."""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# ==============================================================================
# Parsing Utilities
# ==============================================================================

def coerce_num(val: str) -> float:
    """Convert string quantity to float, handling ranges and locales.
    
    Examples:
        >>> coerce_num("2.5")
        2.5
        >>> coerce_num("3-4")
        3.0
        >>> coerce_num("1,5")
        1.5
    """
    if not val:
        return 0
    v = str(val).strip()
    # Handle range -> take first number
    if "-" in v:
        v = v.split("-")[0].strip()
    v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return 0


def parse_nutrition_value(value: str | None) -> float:
    """Extract numeric value from nutrition string like '450 kcal' or '20 g'.
    
    Examples:
        >>> parse_nutrition_value("450 kcal")
        450.0
        >>> parse_nutrition_value("20 g")
        20.0
        >>> parse_nutrition_value(None)
        0
    """
    if not value:
        return 0
    # Extract the first number from the string
    match = re.search(r"(\d+(?:[.,]\d+)?)", str(value))
    if match:
        return float(match.group(1).replace(",", "."))
    return 0


def extract_servings(recipe_data: dict) -> int:
    """Extract numeric servings from recipeYield field.
    
    Args:
        recipe_data: Recipe dictionary containing 'recipeYield' field.
        
    Returns:
        Integer number of servings, defaults to 1 if not found.
    """
    ry = recipe_data.get("recipeYield") or ""
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(ry))
    if m:
        try:
            return int(float(m.group(1).replace(",", ".")))
        except ValueError:
            pass
    return 1


def parse_iso_duration(duration: str) -> int:
    """Parse ISO 8601 duration (e.g., PT30M, PT1H30M) to minutes.
    
    Examples:
        >>> parse_iso_duration("PT30M")
        30
        >>> parse_iso_duration("PT1H30M")
        90
        >>> parse_iso_duration("PT2H")
        120
        
    Returns:
        Minutes as integer, 0 if parsing fails.
    """
    if not duration:
        return 0
    # Match patterns like PT1H30M, PT45M, PT2H
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", str(duration).upper()
    )
    if not match:
        # Try simple numeric (assume minutes)
        try:
            return int(duration)
        except (ValueError, TypeError):
            return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 60 + minutes + (1 if seconds >= 30 else 0)


# ==============================================================================
# Language Utilities
# ==============================================================================

# Map language codes to full names
_LANG_NAMES = {
    "he": "Hebrew",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ar": "Arabic",
    "ru": "Russian",
}


def _get_target_lang() -> str:
    return _LANG_NAMES.get(config.TARGET_LANGUAGE, config.TARGET_LANGUAGE)


def get_recipe_system_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a culinary data normalizer.
Return a single valid JSON object.
MUST be strictly valid JSON (no comments, no trailing commas).
ALL text content MUST be in {target_lang}. Translate any content that is not already in {target_lang}.

If the input is not a recipe (no dish, no ingredients, no cooking steps — e.g. a vlog, ad, music video, or unrelated clip), set "is_recipe" to false, put a short reason in "rejection_reason", and leave the recipe fields empty. Do NOT invent a recipe.

If it is a recipe, set "is_recipe" to true and "rejection_reason" to "".

Required fields:
- "is_recipe" (boolean)
- "rejection_reason" (string; empty when is_recipe is true)
- "name"
- "description" (1–2 short sentences)
- "datePublished" (ISO 8601)
- "recipeYield" (string; empty if unknown)
- "recipeInstructions" (array of objects: {{ "text": "<step>" }})

Ingredients:
- "recipeIngredients" (array of objects). Each item MUST be:
  {{
    "food": "<base ingredient noun in {target_lang}>",
    "quantity": "<number or range as string, or empty string if unknown>",
    "unit": "<unit abbreviation or name in {target_lang}, or empty string if none>",
    "notes": "<prep/brand/extra notes in {target_lang}, or empty string>",
    "raw": "<full ingredient line as shown in recipe, for display fallback>"
  }}
  Rules:
  - "food" MUST be the core ingredient name only (e.g., "flour", "chicken breast", "olive oil").
  - "unit" MUST be a measurement unit only (e.g., "g", "kg", "ml", "cup", "tbsp", "tsp", "piece").
  - Do NOT include modifiers or prep instructions in "food" or "unit" - put them in "notes".
  - Do NOT invent quantities. If missing/unclear → "quantity": "" and "unit": "".
  - If on_screen_text and audio_transcript disagree, trust on_screen_text for ingredients and amounts.
  - A quantity that appears in neither source MUST stay empty.
  - Preserve numeric ranges literally, e.g., "3-4".
  - Put prep words (e.g., קצוץ / chopped, melted, room temperature) into "notes".
  - "raw" should be the complete ingredient line for display purposes.
  - Merge true duplicates (identical food+quantity+unit+notes).

General rules:
- Keep instructions chronological; one step per object.
- Only output the JSON object (no explanations).
- ALL TEXT MUST BE IN {target_lang}.
"""


def get_web_recipe_system_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a culinary data normalizer.
You will receive either:
  (a) a Schema.org Recipe JSON object already extracted from the page, or
  (b) raw visible text scraped from a recipe web page.

Return a single valid JSON object.
MUST be strictly valid JSON (no comments, no trailing commas).
ALL text content MUST be in {target_lang}. Translate any content that is not already in {target_lang}.

If the input is not a recipe (no dish, no ingredients, no cooking steps — e.g. a homepage, blog post, or shop page), set "is_recipe" to false, put a short reason in "rejection_reason", and leave the recipe fields empty. Do NOT invent a recipe.

If it is a recipe, set "is_recipe" to true and "rejection_reason" to "".

Required fields:
- "is_recipe" (boolean)
- "rejection_reason" (string; empty when is_recipe is true)
- "name"
- "description" (1–2 short sentences)
- "datePublished" (ISO 8601)
- "recipeYield" (string; empty if unknown)
- "recipeInstructions" (array of objects: {{ "text": "<step>" }})

Ingredients:
- "recipeIngredients" (array of objects). Each item MUST be:
  {{
    "food": "<base ingredient noun in {target_lang}>",
    "quantity": "<number or range as string, or empty string if unknown>",
    "unit": "<unit abbreviation or name in {target_lang}, or empty string if none>",
    "notes": "<prep/brand/extra notes in {target_lang}, or empty string>",
    "raw": "<full ingredient line as shown in recipe, for display fallback>"
  }}
  Rules:
  - "food" MUST be the core ingredient name only (e.g., "flour", "chicken breast", "olive oil").
  - "unit" MUST be a measurement unit only (e.g., "g", "kg", "ml", "cup", "tbsp", "tsp", "piece").
  - Do NOT include modifiers or prep instructions in "food" or "unit" - put them in "notes".
  - Do NOT invent quantities. If missing/unclear → "quantity": "" and "unit": "".
  - Preserve numeric ranges literally, e.g., "3-4".
  - Put prep words (e.g., chopped, melted, room temperature) into "notes".
  - "raw" should be the complete ingredient line for display purposes.
  - Merge true duplicates (identical food+quantity+unit+notes).

General rules:
- Keep instructions chronological; one step per object.
- Only output the JSON object (no explanations).
- ALL TEXT MUST BE IN {target_lang}.
- If the input already contains Schema.org data, preserve accurate numeric quantities — do not invent or alter them.
- Never guess a missing amount to make the recipe look complete.
"""


def get_yield_nutrition_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a culinary timing assistant.
Given a recipe's ingredients and instructions, estimate:
- servings (number of portions; if unclear, infer a reasonable integer based on ingredient amounts)
- prepTime, cookTime, totalTime as ISO-8601 durations
Leave every nutrition field as "". Do not guess macros; another step looks those up from ingredients.
Assumptions must be realistic; if an item is truly unclear, leave it out.
Return a single valid JSON object with:
{{
  "servings": <int>,
  "recipeYield": "<string in {target_lang}, e.g., '4 מנות' for Hebrew or '4 servings' for English>",
  "prepTime": "PT15M",
  "cookTime": "PT30M",
  "totalTime": "PT45M",
  "nutrition": {{
    "@type": "NutritionInformation",
    "calories": "",
    "proteinContent": "",
    "fatContent": "",
    "carbohydrateContent": "",
    "fiberContent": "",
    "sugarContent": "",
    "sodiumContent": "",
    "cholesterolContent": ""
  }}
}}
Time values must be in ISO 8601 duration format (e.g., "PT30M" for 30 minutes, "PT1H" for 1 hour, "PT1H30M" for 1 hour 30 minutes). Use "" if unknown.
Servings 1–50 only. Output recipeYield in {target_lang}.
"""


# ==============================================================================
# Mealie library optimization prompts
# ==============================================================================

def get_duplicate_check_prompt() -> str:
    return """You are a culinary librarian deciding whether recipes are duplicates.

You will receive two or more recipes that a name/ingredient similarity check already flagged as close. Decide whether they are the SAME DISH, such that keeping only one of them loses nothing meaningful.

Answer true only when they are genuinely interchangeable:
- the same dish imported twice, or re-uploaded with a tidier title
- the same dish with trivial wording differences ("Spaghetti Bolognese" vs "Spaghetti Bolognese - classic")
- the same dish where one copy is simply more complete than the other

Answer false when they are distinct dishes that merely sound alike:
- a different preparation of the same core ingredient (roast chicken vs chicken soup)
- a variant that changes the dish (chocolate cake vs chocolate mousse cake, beef lasagna vs vegetarian lasagna)
- the same dish name from clearly different cuisines or techniques

When in doubt, answer false. A wrong "true" gets a real recipe deleted.

Return a single valid JSON object:
{
  "same_dish": <boolean>,
  "confidence": <number between 0 and 1>,
  "reason": "<one short sentence in English explaining the decision>"
}
"""


def get_recipe_tagging_prompt(allowed_tags: list[str]) -> str:
    tag_list = "\n".join(f"- {tag}" for tag in allowed_tags)
    return f"""You are a recipe cataloguer assigning tags from a fixed vocabulary.

You will receive a recipe's name, description and ingredients, plus the tags it already has.

Choose every tag from the list below that genuinely applies. A recipe usually has two to five tags: one or two about the main ingredient or diet, one about the cuisine if it is clearly identifiable, and one about the course or occasion.

ALLOWED TAGS - you may use these and nothing else:
{tag_list}

Rules:
- Use the tag strings exactly as written above. Never invent, translate, pluralise or rephrase a tag.
- Do NOT repeat tags the recipe already has; suggest only additions.
- Only tag a cuisine when the dish clearly belongs to it. A tomato does not make a dish Italian.
- Only tag a diet restriction when every ingredient supports it. "vegan" requires no animal product at all; "dairy" requires an actual dairy ingredient.
- If nothing applies, return an empty list. An empty list is a valid, useful answer.

Return a single valid JSON object:
{{
  "tags": [
    {{ "tag": "<tag exactly as listed above>", "reason": "<short English justification>" }}
  ]
}}
"""


def get_recipe_audit_prompt() -> str:
    target_lang = _get_target_lang()
    return f"""You are a recipe editor checking that a recipe can actually be cooked as written.

You will receive a recipe's ingredients and its ordered instructions. Find the places where the recipe does not hold together, and return a corrected version.

Look for exactly these problems:
- "ingredient_not_used": an ingredient is listed but no step ever uses it.
- "ingredient_missing": a step uses something that is not in the ingredient list.
- "step_missing": the flow skips a necessary action (e.g. it bakes without ever preheating, or serves a sauce that was never combined).
- "step_out_of_order": steps are sequenced so the recipe cannot be followed as written.
- "quantity_missing": an ingredient has no usable amount and the step does not imply one.

Rules for the correction:
- NEVER delete an ingredient or a step. You may add, reorder, merge wording, and fill in missing amounts.
- "corrected_ingredients" MUST contain every ingredient from the input, plus any you added. Same for "corrected_steps".
- Do not invent a quantity that the recipe does not support. If an amount is genuinely unknowable, keep the ingredient without one and report it as "quantity_missing" instead of guessing.
- Keep the author's voice. Rewrite only what is needed to fix the problem you reported.
- ALL text you output MUST be in {target_lang}, matching the input.
- If the recipe is already sound, return an empty "issues" list and echo the input unchanged in "corrected_ingredients" and "corrected_steps".

Return a single valid JSON object:
{{
  "summary": "<one short sentence in {target_lang} describing the state of the recipe>",
  "issues": [
    {{
      "kind": "<one of: ingredient_not_used, ingredient_missing, step_missing, step_out_of_order, quantity_missing>",
      "detail": "<what is wrong, in {target_lang}>",
      "target": "<the ingredient line or step text this concerns>",
      "suggestion": "<the concrete fix, in {target_lang}>"
    }}
  ],
  "corrected_ingredients": ["<full ingredient line>", "..."],
  "corrected_steps": ["<full step text>", "..."]
}}
"""
