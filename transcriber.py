import os
import subprocess
import sys
import time
from faster_whisper import WhisperModel

from ai_providers import AiSession
from config import config
from helpers import setup_logger
from recipe_schema import (
    VISUAL_JSON_SCHEMA,
    VisualTextExtraction,
    parse_visual_text,
)

logger = setup_logger(__name__)


class Transcriber:
    def __init__(
        self,
        video_path: str,
        model_size: str | None = None,  # tiny | base | small | medium | large-v3 (defaults to config.WHISPER_MODEL)
        device: str = "auto",           # "cpu" or "cuda"
        compute_type: str = "auto",     # "float16"/"int8"/"auto"
    ):
        self.video_path = video_path
        self.model_size = model_size or config.WHISPER_MODEL
        self.device = device
        self.compute_type = compute_type
        self.model = None
        self.audio_path = self._get_audio_path()

    def _get_audio_path(self):
        # Store audio in the same dish folder as the video
        dish_dir = os.path.dirname(self.video_path)
        return os.path.join(dish_dir, "audio.wav")

    def _get_video_duration(self) -> float:
        """Get video duration in seconds using ffprobe."""
        return self._get_file_duration(self.video_path)

    def _has_audio_stream(self) -> bool:
        """Return True if the video file contains at least one audio stream."""
        probe_cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            self.video_path,
        ]
        try:
            result = subprocess.run(
                probe_cmd, capture_output=True, text=True, check=True,
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return False

    def _extract_audio(self, overwrite: bool = False):
        """Extract mono WAV audio at 16kHz using ffmpeg with progress indicator."""
        if os.path.exists(self.audio_path) and not overwrite:
            logger.info("[Transcribe] Using cached audio file.")
            return self.audio_path

        if not self._has_audio_stream():
            logger.warning(
                "[Transcribe] Video has no audio stream; skipping audio extraction."
            )
            return None

        # Get video duration for progress estimation
        duration = self._get_video_duration()
        logger.info(f"[Transcribe] Extracting audio from video ({duration:.1f}s)...")

        cmd = [
            "ffmpeg",
            "-y", "-i", self.video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "-progress", "pipe:1",  # Output progress to stdout
            self.audio_path
        ]
        
        # Run ffmpeg with progress tracking
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        # Track progress
        start_time = time.time()
        current_time = 0.0
        
        stderr = ""
        if process.stdout:
            for line in process.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        out_time_ms = int(line.split("=")[1])
                        current_time = out_time_ms / 1_000_000  # Convert to seconds
                        if duration > 0:
                            progress = min(100, (current_time / duration) * 100)
                            elapsed = time.time() - start_time
                            # Print progress bar
                            bar_width = 30
                            filled = int(bar_width * progress / 100)
                            bar = "█" * filled + "░" * (bar_width - filled)
                            sys.stdout.write(f"\r[Transcribe] Extracting audio: [{bar}] {progress:.0f}% ({elapsed:.1f}s)")
                            sys.stdout.flush()
                    except (ValueError, IndexError):
                        pass
                elif line == "progress=end":
                    break

        process.wait()
        if process.stderr:
            stderr = process.stderr.read()
        
        # Clear progress line and print completion
        if duration > 0:
            elapsed = time.time() - start_time
            if process.returncode == 0:
                sys.stdout.write(f"\r[Transcribe] Audio extraction complete ({elapsed:.1f}s)                    \n")
                sys.stdout.flush()
        
        if process.returncode != 0:
            stderr_tail = (stderr or "").strip().splitlines()[-3:]
            detail = " ".join(stderr_tail) if stderr_tail else "unknown error"
            no_audio = (
                process.returncode == 234
                or "does not contain any stream" in (stderr or "")
            )
            if no_audio:
                logger.warning(
                    "[Transcribe] Video has no usable audio stream; skipping audio extraction."
                )
                return None
            raise RuntimeError(
                f"ffmpeg audio extraction failed (exit {process.returncode}): {detail}"
            ) from None
        
        logger.info(f"[Transcribe] Audio saved to: {self.audio_path}")
        return self.audio_path

    def _load_model(self):
        if self.model is None:
            from whisper_pool import get_whisper_model
            self.model = get_whisper_model(
                self.model_size, device=self.device, compute_type=self.compute_type)

    def _get_audio_duration(self) -> float:
        """Get audio file duration in seconds using ffprobe."""
        if not os.path.exists(self.audio_path):
            return 0.0
        return self._get_file_duration(self.audio_path)

    def _get_file_duration(self, file_path: str) -> float:
        """Get media file duration in seconds using ffprobe."""
        duration_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path
        ]
        try:
            result = subprocess.run(duration_cmd, capture_output=True, text=True, check=True)
            return float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return 0.0

    def transcribe(self, language: str | None = None) -> str:
        """Return the full transcription as plain text.

        Runs Whisper on this machine unless Settings names a provider to
        transcribe through. A provider that fails falls back to the local
        model rather than losing the audio entirely.

        Args:
            language: Language code for transcription (e.g., 'he', 'en').
                     Defaults to config.TARGET_LANGUAGE if not specified.
        """
        audio_path = self._extract_audio()
        if audio_path is None:
            logger.info(
                "[Transcribe] No audio available; continuing with visual text only."
            )
            return ""

        lang = language or config.TARGET_LANGUAGE

        remote = self._transcribe_with_provider(audio_path, lang)
        if remote is not None:
            return remote

        return self._transcribe_locally(audio_path, lang)

    def _transcribe_with_provider(self, audio_path: str, lang: str) -> str | None:
        """Transcription from the configured provider, or None to use Whisper."""
        from ai_providers import transcribe_audio, transcription_provider

        provider = transcription_provider()
        if provider is None:
            return None
        try:
            logger.info(
                "[Transcribe] Transcribing through %s (%s)",
                provider.label, config.TRANSCRIPTION_MODEL,
            )
            return transcribe_audio(
                provider, audio_path,
                model=config.TRANSCRIPTION_MODEL, language=lang,
            )
        except Exception as exc:
            logger.warning(
                "[Transcribe] %s could not transcribe (%s); falling back to "
                "local Whisper.", provider.label, exc,
            )
            return None

    def _transcribe_locally(self, audio_path: str, lang: str) -> str:
        """faster-whisper on this machine, which needs no API and no network."""
        self._load_model()
        segments, _info = self.model.transcribe(audio_path, language=lang)
        return " ".join(seg.text.strip() for seg in segments).strip()

    def extract_visual_text(self) -> str:
        """Extract on-screen text from the video using the configured provider.

        Providers that can read a video directly (Gemini) get the file; the
        rest are handed evenly spaced frames. Which of the two happens is the
        only thing this method still decides - every dialect difference lives
        in ai_providers.

        Returns:
            Extracted text from video as a single string.
        """
        session = AiSession()
        prompt = self._get_visual_text_prompt()

        if session.supports_video_upload:
            raw = session.complete_json_with_video(
                prompt,
                self.video_path,
                schema_name="visual_text",
                json_schema=VISUAL_JSON_SCHEMA,
                schema_model=VisualTextExtraction,
            )
            return _plain_visual_text(raw)

        frames = self._extract_frames(num_frames=8)
        if not frames:
            raise RuntimeError("No frames could be extracted from video")
        raw = session.complete_json_with_images(
            prompt,
            frames,
            schema_name="visual_text",
            json_schema=VISUAL_JSON_SCHEMA,
            schema_model=VisualTextExtraction,
            # On-screen text is small and often stylised, so it needs the
            # detailed read that frame selection does not.
            detail="high",
        )
        return _plain_visual_text(raw)

    def _extract_frames(self, num_frames: int = 8) -> list[str]:
        """Extract evenly-spaced frames from video using ffmpeg."""
        dish_dir = os.path.dirname(self.video_path)
        frames_dir = os.path.join(dish_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        # Get video duration
        duration_cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            self.video_path
        ]

        try:
            result = subprocess.run(
                duration_cmd, capture_output=True, text=True, check=True)
            duration = float(result.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            duration = 30.0  # Default assumption

        # Calculate timestamps
        margin = min(0.5, duration * 0.05)
        interval = (duration - 2 * margin) / (num_frames - 1)
        timestamps = [margin + (i * interval) for i in range(num_frames)]

        frame_paths = []
        for i, ts in enumerate(timestamps):
            frame_path = os.path.join(frames_dir, f"frame_{i:02d}.jpg")

            if not os.path.exists(frame_path):
                cmd = [
                    "ffmpeg", "-y", "-ss", str(ts),
                    "-i", self.video_path,
                    "-vframes", "1",
                    "-q:v", "2",
                    frame_path
                ]
                subprocess.run(cmd, capture_output=True, check=True)

            frame_paths.append(frame_path)

        return frame_paths

    def _get_visual_text_prompt(self) -> str:
        """Get the prompt for visual text extraction."""
        lang_names = {
            "he": "Hebrew",
            "en": "English",
            "es": "Spanish",
            "fr": "French",
            "de": "German",
            "it": "Italian",
            "ar": "Arabic",
            "ru": "Russian",
        }
        target_lang = lang_names.get(
            config.TARGET_LANGUAGE, config.TARGET_LANGUAGE)

        return f"""Analyze this video/images and extract ALL text that appears on screen.
This includes:
- Recipe titles and names
- Ingredient lists with quantities
- Cooking instructions or steps
- Captions or subtitles
- Any overlay text, annotations, or labels
- Timer displays or temperatures

Return a JSON object with:
- "title": recipe name or empty string
- "ingredients_text": ingredient lines, one per line (empty if none)
- "instructions_text": cooking steps (empty if none)
- "other_text": any remaining on-screen text
If text appears multiple times, include it once.
Output the text in {target_lang} language. If the original text is in a different language, translate it to {target_lang}."""


def _plain_visual_text(raw: str) -> str:
    """Prefer structured OCR JSON; fall back to the raw model text."""
    try:
        return parse_visual_text(raw).as_plain_text() or raw
    except Exception:
        return raw
