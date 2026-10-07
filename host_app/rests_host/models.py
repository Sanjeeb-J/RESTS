"""Local model storage.

Weights live under ``host_app/models`` (override with ``RESTS_MODEL_DIR``)
which is ignored by Git, so the multi-gigabyte checkpoints never enter the
repository.  ``download_models.py`` is the only code path allowed to touch
the network; the application itself always loads with ``local_files_only``.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_MODEL_DIR = "RESTS_MODEL_DIR"

#: Default faster-whisper checkpoint (multilingual, ~1.5 GB).
DEFAULT_WHISPER_MODEL = "Systran/faster-whisper-medium"

#: Default translation checkpoint (~2.4 GB, see the NLLB licence note in
#: :mod:`rests_host.translate`).
DEFAULT_NLLB_MODEL = "facebook/nllb-200-distilled-600M"

#: Environment switches honoured by the application.
ENV_WHISPER_MODEL = "RESTS_WHISPER_MODEL"
ENV_NLLB_MODEL = "RESTS_NLLB_MODEL"
ENV_TRANSLATION_BACKEND = "RESTS_TRANSLATION_BACKEND"


def model_root() -> Path:
    """Directory that holds every downloaded model."""
    override = os.environ.get(ENV_MODEL_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "models"


def whisper_model_name() -> str:
    return os.environ.get(ENV_WHISPER_MODEL) or DEFAULT_WHISPER_MODEL


def nllb_model_name() -> str:
    return os.environ.get(ENV_NLLB_MODEL) or DEFAULT_NLLB_MODEL


def model_root_is_writable() -> bool:
    root = model_root()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(root, os.W_OK)
