"""Shared Whisper model pool — load each model size once per process."""

import os
import threading
from typing import Tuple

from faster_whisper import WhisperModel

from config import config
from helpers import setup_logger

logger = setup_logger(__name__)

_lock = threading.Lock()
_models: dict[Tuple[str, str, str], WhisperModel] = {}


def get_whisper_model(
    model_size: str | None = None,
    device: str = "auto",
    compute_type: str = "auto",
) -> WhisperModel:
    """Return a cached WhisperModel, loading on first use."""
    size = model_size or config.WHISPER_MODEL
    key = (size, device, compute_type)

    with _lock:
        if key not in _models:
            if config.HF_TOKEN:
                os.environ["HF_TOKEN"] = config.HF_TOKEN
            logger.info("[WhisperPool] Loading model %s (device=%s)", size, device)
            _models[key] = WhisperModel(size, device=device, compute_type=compute_type)
        return _models[key]


def clear_pool() -> None:
    """Release cached models (mainly for tests)."""
    with _lock:
        _models.clear()
