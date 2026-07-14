import argparse
import json
from helpers import setup_logger
from pipeline import PipelineStats, run_extraction_pipeline

logger = setup_logger(__name__)


class CliReporter:
    def __init__(self):
        self._cancelled = False

    def is_cancelled(self):
        return self._cancelled

    def update(self, stage, message, percent, video_title=None):
        logger.info("[%s] %s (%s%%)", stage, message, percent)


def main(video_url: str, *, skip_upload: bool = False):
    stats = PipelineStats()
    result = run_extraction_pipeline(
        video_url, CliReporter(), work_dir="tmp", stats=stats, skip_upload=skip_upload,
    )
    if result.error:
        logger.error("Pipeline failed: %s", result.error)
        return None
    return {
        "transcription": "",
        "description": result.recipe_data.get("description", "") if result.recipe_data else "",
        "image_path": result.image_path,
        "recipe_data": result.recipe_data,
        "llm_tokens_estimate": result.llm_tokens_estimate,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract recipe from video (supports TikTok, YouTube, Instagram, etc.)")
    parser.add_argument("url", nargs="?",
                        default="https://www.tiktok.com/@recipeincaption/video/7532985862854921477",
                        help="Video URL (TikTok, YouTube, Instagram, etc.)")
    parser.add_argument("--no-upload", action="store_true",
                        help="Skip uploading to recipe manager (for testing)")
    args = parser.parse_args()
    
    url = args.url
    video_results = main(url, skip_upload=args.no_upload)
    if not video_results or not video_results.get("recipe_data"):
        logger.warning("[AI Recipe] No recipe created.")
        recipe_data = None
    else:
        recipe_data = video_results["recipe_data"]
        logger.info(
            "[AI Recipe] Complete (est. tokens: %s).",
            video_results.get("llm_tokens_estimate"),
        )

    if recipe_data:
        logger.info(json.dumps(recipe_data, ensure_ascii=False, indent=2))
