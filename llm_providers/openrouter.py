"""
OpenRouter LLM provider for image selection.

Uses OpenRouter's OpenAI-compatible Chat Completions API with a vision-capable
model. The configured model must be a vision model (e.g. ``openai/gpt-4o-mini``
or ``google/gemini-2.5-flash``).
"""

import base64

from .base import LLMImageSelector
from config import config
from helpers import setup_logger
from llm_resilience import call_with_model_fallback

logger = setup_logger(__name__)


class OpenRouterImageSelector(LLMImageSelector):
    """Image selector using an OpenRouter vision model."""

    def select_best_frame(self, frame_paths: list[str]) -> int | None:
        """
        Select best frame using an OpenRouter vision model.

        Args:
            frame_paths: List of paths to candidate frame images.

        Returns:
            Index of the best frame, or None if selection fails.
        """
        from llm_openrouter import make_openrouter_client

        client = make_openrouter_client()

        # Encode frames as base64 data URLs (OpenAI Chat Completions vision format)
        image_contents: list[dict] = []
        for i, frame_path in enumerate(frame_paths):
            with open(frame_path, "rb") as f:
                b64_image = base64.standard_b64encode(f.read()).decode("utf-8")
            image_contents.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64_image}",
                    "detail": "low",  # Use low detail for faster selection
                },
            })
            image_contents.append({
                "type": "text",
                "text": f"[Image {i}]",
            })

        prompt = self._get_selection_prompt(len(frame_paths))
        image_contents.append({"type": "text", "text": prompt})

        def _call(model: str):
            from recipe_schema import FRAME_JSON_SCHEMA

            return client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": image_contents}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "frame_selection",
                        "strict": True,
                        "schema": FRAME_JSON_SCHEMA,
                    },
                },
            )

        try:
            response, _ = call_with_model_fallback(
                "openrouter", config.OPENROUTER_MODEL, _call
            )
            content = response.choices[0].message.content or ""
            return self._parse_selection_response(content, len(frame_paths))
        except Exception as e:
            logger.error(f"Selection error: {e}")
            return None
