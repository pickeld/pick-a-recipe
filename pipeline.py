"""
Shared video-to-recipe extraction pipeline used by the web UI and CLI.
"""

from __future__ import annotations

import base64
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from config import config
from image_extractor import extract_dish_image_candidates
from transcriber import Transcriber
from video_downloader import VideoDownloader


class ProgressReporter(Protocol):
    def is_cancelled(self) -> bool: ...
    def update(self, stage: str, message: str, percent: int, video_title: str | None = None) -> None: ...


@dataclass
class PipelineResult:
    recipe_data: dict | None = None
    image_path: str | None = None
    output_target: str = ""
    llm_tokens_estimate: int = 0
    error: str | None = None


@dataclass
class PipelineStats:
    llm_tokens_estimate: int = 0

    def add_text(self, text: str) -> None:
        # Rough token estimate (~4 chars per token) for cost tracking
        self.llm_tokens_estimate += max(1, len(text) // 4)


@dataclass
class PreviewWaiter:
    """Handles optional confirm-before-upload flow."""

    job_id: str
    recipe_data: dict
    image_path: str | None
    image_candidates: list
    best_image_index: int
    output_target: str
    export_to_both: bool
    emit_preview: Callable[[dict], None]
    wait_for_confirmation: Callable[[str, threading.Event, int], tuple[bool, int]]
    pending_uploads: dict
    create_pending_upload_fn: Callable
    get_pending_upload_fn: Callable
    delete_pending_upload_fn: Callable
    is_cancelled: Callable[[], bool]
    socketio_emit_cancelled: Callable[[], None]


def run_extraction_pipeline(
    url: str,
    reporter: ProgressReporter,
    *,
    work_dir: str = "/tmp",
    stats: PipelineStats | None = None,
    preview: PreviewWaiter | None = None,
    skip_upload: bool = False,
) -> PipelineResult:
    """Run the full download → transcribe → recipe pipeline."""
    stats = stats or PipelineStats()

    try:
        config.reload()

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("info", "Fetching video information...", 10)
        downloader = VideoDownloader(url)
        item = downloader._get_info()
        description = item.get("description", "No description available.")
        title = item.get("title", "Untitled")
        reporter.update("info", f"Video: {title}", 15, video_title=title)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("download", "Downloading video...", 20)
        vid_id, video_path = downloader._download_video()
        if vid_id is None:
            return PipelineResult(error="Failed to download video")

        dish_dir = os.path.join(work_dir, vid_id)
        reporter.update("download", "Video downloaded successfully", 30)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("transcribe", "Transcribing audio...", 35)
        transcriber = Transcriber(video_path)
        lang = config.TARGET_LANGUAGE
        audio_cache = os.path.join(dish_dir, f"transcription_{lang}.txt")

        if os.path.exists(audio_cache):
            reporter.update("transcribe", "Using cached transcription", 40)
            with open(audio_cache, "r", encoding="utf-8") as f:
                transcription = f.read()
        else:
            transcription = transcriber.transcribe()
            with open(audio_cache, "w", encoding="utf-8") as f:
                f.write(transcription)
        stats.add_text(transcription)
        reporter.update("transcribe", "Audio transcribed", 50)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("visual", "Extracting on-screen text...", 55)
        visual_text = ""
        visual_cache = os.path.join(dish_dir, f"visual_{lang}.txt")
        if os.path.exists(visual_cache):
            reporter.update("visual", "Using cached visual text", 60)
            with open(visual_cache, "r", encoding="utf-8") as f:
                visual_text = f.read()
        else:
            try:
                visual_text = transcriber.extract_visual_text()
                with open(visual_cache, "w", encoding="utf-8") as f:
                    f.write(visual_text)
                stats.add_text(visual_text)
            except Exception as exc:
                reporter.update("visual", f"Warning: Could not extract visual text: {exc}", 60)
        reporter.update("visual", "Visual text extracted", 65)

        combined_transcription = transcription
        if visual_text:
            combined_transcription = (
                f"=== AUDIO TRANSCRIPTION ===\n{transcription}\n\n"
                f"=== ON-SCREEN TEXT (ingredients, instructions, etc.) ===\n{visual_text}"
            )

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("image", "Extracting dish image candidates...", 70)
        image_path = None
        image_candidates: list[str] = []
        best_image_index = 0
        image_cache = os.path.join(dish_dir, "dish.jpg")
        frames_dir = os.path.join(dish_dir, "dish_frames")

        if os.path.exists(frames_dir) and os.path.exists(image_cache):
            reporter.update("image", "Using cached dish images", 75)
            image_path = image_cache
            image_candidates = sorted(
                os.path.join(frames_dir, f)
                for f in os.listdir(frames_dir)
                if f.startswith("dish_candidate_") and f.endswith(".jpg")
            )
        else:
            try:
                result = extract_dish_image_candidates(video_path)
                image_path = result.get("best_image")
                image_candidates = result.get("candidates", [])
                best_image_index = result.get("best_index", 0)
            except Exception as exc:
                reporter.update("image", f"Warning: Could not extract image: {exc}", 75)
        reporter.update("image", "Image candidates extracted", 80)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        reporter.update("evaluate", "Creating recipe with AI...", 85)
        from chef import Chef

        chef = Chef(source_url=url, description=description, transcription=combined_transcription)
        stats.add_text(combined_transcription)
        recipe_data = chef.create_recipe()
        if not recipe_data:
            return PipelineResult(error="Failed to create recipe", llm_tokens_estimate=stats.llm_tokens_estimate)

        reporter.update("evaluate", "Recipe created successfully", 90)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled")

        if config.CONFIRM_BEFORE_UPLOAD and preview is not None:
            selected_idx = _handle_preview_confirmation(preview, recipe_data, image_path,
                                                        image_candidates, best_image_index, reporter)
            if selected_idx is None:
                return PipelineResult(error="cancelled", llm_tokens_estimate=stats.llm_tokens_estimate)
            if image_candidates and 0 <= selected_idx < len(image_candidates):
                image_path = image_candidates[selected_idx]
            reporter.update("upload", f"Uploading to {config.OUTPUT_TARGET}...", 95)
        else:
            reporter.update("upload", f"Uploading to {config.OUTPUT_TARGET}...", 95)

        if reporter.is_cancelled():
            return PipelineResult(error="cancelled", recipe_data=recipe_data, image_path=image_path,
                                  llm_tokens_estimate=stats.llm_tokens_estimate)

        if skip_upload:
            reporter.update("complete", "Recipe created (upload skipped)", 100)
            return PipelineResult(
                recipe_data=recipe_data,
                image_path=image_path,
                output_target="none",
                llm_tokens_estimate=stats.llm_tokens_estimate,
            )

        upload_targets = ["tandoor", "mealie"] if config.EXPORT_TO_BOTH else [config.OUTPUT_TARGET]
        if config.EXPORT_TO_BOTH:
            reporter.update("upload", "Uploading to Tandoor and Mealie...", 95)

        upload_results = []
        for target in upload_targets:
            try:
                if target == "tandoor":
                    from tandoor import Tandoor
                    tandoor = Tandoor()
                    result = tandoor.create_recipe(recipe_data)
                    if image_path and result.get("id"):
                        tandoor.upload_image(result["id"], image_path)
                    upload_results.append((target, True, None))
                elif target == "mealie":
                    from mealie import Mealie
                    mealie = Mealie()
                    result = mealie.create_recipe(recipe_data)
                    recipe_slug = result.get("slug") or result.get("id")
                    if image_path and recipe_slug:
                        mealie.upload_image(recipe_slug, image_path)
                    upload_results.append((target, True, None))
            except Exception as upload_error:
                upload_results.append((target, False, str(upload_error)))

        final_target = ", ".join(upload_targets) if config.EXPORT_TO_BOTH else config.OUTPUT_TARGET
        failed = [r for r in upload_results if not r[1]]
        if failed and len(failed) == len(upload_targets):
            msgs = "; ".join(f"{r[0]}: {r[2]}" for r in failed)
            return PipelineResult(error=f"All uploads failed: {msgs}", recipe_data=recipe_data,
                                  image_path=image_path, llm_tokens_estimate=stats.llm_tokens_estimate)

        if failed:
            success = [r[0] for r in upload_results if r[1]]
            final_target = ", ".join(success)
            failed_msgs = "; ".join(f"{r[0]}: {r[2]}" for r in failed)
            reporter.update(
                "complete",
                f"Uploaded to {final_target}. Failed: {failed_msgs}",
                100,
            )
        else:
            reporter.update("complete", f"Recipe uploaded successfully to {final_target}!", 100)
        return PipelineResult(
            recipe_data=recipe_data,
            image_path=image_path,
            output_target=final_target,
            llm_tokens_estimate=stats.llm_tokens_estimate,
        )

    except Exception as exc:
        return PipelineResult(error=f"Error: {exc}", llm_tokens_estimate=stats.llm_tokens_estimate)


def _handle_preview_confirmation(
    preview: PreviewWaiter,
    recipe_data: dict,
    image_path: str | None,
    image_candidates: list,
    best_image_index: int,
    reporter: ProgressReporter,
) -> int | None:
    """Show preview and wait for user confirmation. Returns selected index or None if cancelled."""
    reporter.update("preview", "Waiting for your confirmation...", 90)

    image_data = None
    if image_path and os.path.exists(image_path):
        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode("utf-8")

    candidate_images_data = []
    for idx, candidate_path in enumerate(image_candidates):
        if os.path.exists(candidate_path):
            with open(candidate_path, "rb") as f:
                candidate_images_data.append({
                    "index": idx,
                    "data": base64.b64encode(f.read()).decode("utf-8"),
                    "path": candidate_path,
                    "is_best": idx == best_image_index,
                })

    display_target = "Tandoor & Mealie" if preview.export_to_both else preview.output_target.capitalize()
    confirm_event = threading.Event()
    upload_id = secrets.token_hex(16)

    preview.pending_uploads[upload_id] = {
        "recipe": recipe_data,
        "image_path": image_path,
        "image_candidates": image_candidates,
        "output_target": preview.output_target,
        "event": confirm_event,
        "confirmed": None,
        "selected_image_index": best_image_index,
        "job_id": preview.job_id,
    }

    preview.create_pending_upload_fn(
        upload_id=upload_id,
        job_id=preview.job_id,
        recipe_data=recipe_data,
        image_path=image_path,
        image_candidates=image_candidates,
        output_target=preview.output_target,
        best_image_index=best_image_index,
        timeout_minutes=5,
    )

    preview.emit_preview({
        "job_id": preview.job_id,
        "upload_id": upload_id,
        "recipe": recipe_data,
        "image_data": image_data,
        "candidate_images": candidate_images_data,
        "best_image_index": best_image_index,
        "output_target": display_target,
        "export_to_both": preview.export_to_both,
    })

    timeout_seconds = 300
    poll_interval = 1
    elapsed = 0
    confirmed = False
    db_confirmed = False
    selected_idx = best_image_index

    while elapsed < timeout_seconds:
        if confirm_event.wait(timeout=poll_interval):
            confirmed = True
            break

        db_upload = preview.get_pending_upload_fn(upload_id)
        if db_upload:
            if db_upload["status"] == "confirmed":
                db_confirmed = True
                confirmed = True
                selected_idx = db_upload.get("selected_image_index", best_image_index)
                break
            if db_upload["status"] in ("cancelled", "expired"):
                break

        elapsed += poll_interval
        if preview.is_cancelled():
            preview.delete_pending_upload_fn(upload_id)
            preview.pending_uploads.pop(upload_id, None)
            return None

    db_upload = preview.get_pending_upload_fn(upload_id)
    preview.delete_pending_upload_fn(upload_id)
    pending_data = preview.pending_uploads.pop(upload_id, None)

    if not confirmed and elapsed >= timeout_seconds:
        return None

    was_confirmed = False
    if db_confirmed:
        was_confirmed = db_upload and db_upload["status"] == "confirmed"
    elif pending_data:
        was_confirmed = pending_data.get("confirmed", False)

    if not was_confirmed:
        preview.socketio_emit_cancelled()
        return None

    if not db_confirmed and pending_data:
        selected_idx = pending_data.get("selected_image_index", best_image_index)
    return selected_idx
