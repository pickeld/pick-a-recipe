"""Pick the frame that best represents the finished dish.

One implementation for every provider: the vision call goes through
ai_providers, so this module only has to say what makes a good frame and read
the answer back.
"""

from __future__ import annotations

import re

from ai_providers import AiSession
from helpers import setup_logger
from recipe_schema import FRAME_JSON_SCHEMA, FrameSelection, parse_frame_selection

logger = setup_logger(__name__)


def selection_prompt(num_frames: int) -> str:
    return f"""You are analyzing {num_frames} frames from a cooking video to find the BEST image that describes and represents the dish being made.

Select the frame that best:
1. DESCRIBES THE DISH - Shows what the dish actually is (ingredients, style, cuisine type are visible/recognizable)
2. REPRESENTS THE FINAL RESULT - Shows the completed/finished dish, not preparation steps
3. IDENTIFIES THE FOOD - A viewer can clearly understand what dish this is just by looking at the image
4. Shows appetizing presentation with good lighting and the food as the main subject
5. Has clear focus and attractive plating where the dish's key characteristics are visible

PRIORITY: Choose the image that someone could look at and immediately understand "this is [dish name]". The image should capture the essence and identity of the dish.

Return JSON {{"index": N}} where N is the 0-based frame number (0-{num_frames - 1}).
If none show a finished dish, pick the frame that best describes what food is being made."""


def parse_selection(response: str, max_idx: int) -> int | None:
    """Frame index out of the model's answer, or None if it did not give one."""
    structured = parse_frame_selection(response, max_idx)
    if structured is not None:
        return structured
    # Some models answer a schema request with prose anyway; the first integer
    # in range is better than discarding a usable answer.
    match = re.search(r"\d+", (response or "").strip())
    if match:
        index = int(match.group())
        if 0 <= index < max_idx:
            return index
    return None


def select_best_frame(frame_paths: list[str]) -> int | None:
    """Index of the best frame, or None when selection fails."""
    if not frame_paths:
        return None
    try:
        raw = AiSession().complete_json_with_images(
            selection_prompt(len(frame_paths)),
            frame_paths,
            schema_name="frame_selection",
            json_schema=FRAME_JSON_SCHEMA,
            schema_model=FrameSelection,
            # Enough to tell a plated dish from a chopping board, and far
            # cheaper than a detailed read of every candidate.
            detail="low",
            label_images=True,
        )
    except Exception as exc:
        logger.error("[Frames] Selection failed: %s", exc)
        return None
    return parse_selection(raw, len(frame_paths))
