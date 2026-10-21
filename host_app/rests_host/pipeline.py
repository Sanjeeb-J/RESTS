"""Recording -> transcription -> translation -> cleanup.

This module owns the temporary WAV file: it is created for the duration of
one job and deleted in a ``finally`` block, so no audio survives inside
the repository (``host_app/tmp`` is Git-ignored) even when a step fails.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .asr import AsrEngine
from .audio import (
    DEFAULT_SAMPLE_RATE,
    flatten_chunks,
    recording_seconds,
    remove_quietly,
    temp_recording_dir,
    write_wav,
)
from .languages import Language
from .translate import TranslationService


@dataclass(frozen=True)
class ProcessingResult:
    source_text: str
    translated_text: str
    detected_language: Optional[str]
    ok: bool
    error: str = ""
    translation_ok: bool = False
    translation_error: str = ""
    language_key: str = ""
    target_key: str = ""
    duration_seconds: float = 0.0
    #: Backend notices (recogniser and/or translation) such as an
    #: automatic CUDA -> CPU fallback; empty when nothing changed.
    asr_notice: str = ""


def _notices(asr: AsrEngine, translation: TranslationService) -> str:
    """Concatenate backend notices (CUDA -> CPU fallbacks, ...)."""
    parts = [
        str(getattr(asr, "notice", "")),
        str(getattr(translation, "notice", "")),
    ]
    return " | ".join(part for part in parts if part)


def process_recording(
    samples: Sequence[float],
    asr: AsrEngine,
    translation: TranslationService,
    source_language: Language,
    target_language: Language,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    tmp_dir: Optional[Path] = None,
    audio_relay: Optional[Callable[[Sequence[float]], Sequence[float]]] = None,
) -> ProcessingResult:
    """Run one full job. Always removes the temporary recording."""
    directory = Path(tmp_dir) if tmp_dir else temp_recording_dir()
    path = directory / f"rests_{int(time.time())}_{uuid.uuid4().hex[:8]}.wav"
    started = time.monotonic()
    try:
        # When configured by the app, speech recognition must use the audio
        # that completed the Renode/STM32 UART round-trip.
        model_samples = audio_relay(samples) if audio_relay is not None else samples
        if len(model_samples) == 0:
            raise RuntimeError("the STM32 audio relay returned no audio samples")
        write_wav(path, model_samples, sample_rate)
        transcript = asr.transcribe(str(path))
        duration = recording_seconds(len(samples), sample_rate)
        if not transcript.ok:
            return ProcessingResult(
                source_text="",
                translated_text="",
                detected_language=transcript.language,
                ok=False,
                error=transcript.error,
                duration_seconds=duration,
                asr_notice=_notices(asr, translation),
            )
        translated = translation.translate(
            transcript.text,
            source_language,
            target_language,
            detected_language=transcript.language,
        )
        return ProcessingResult(
            source_text=transcript.text,
            translated_text=translated.text if translated.ok else transcript.text,
            detected_language=transcript.language,
            ok=True,
            translation_ok=translated.ok,
            translation_error=translated.error,
            language_key=translated.source_key or (transcript.language or ""),
            target_key=target_language.key,
            duration_seconds=duration,
            asr_notice=_notices(asr, translation),
        )
    except Exception as exc:
        return ProcessingResult(
            source_text="",
            translated_text="",
            detected_language=None,
            ok=False,
            error=str(exc),
            duration_seconds=time.monotonic() - started,
            asr_notice=_notices(asr, translation),
        )
    finally:
        remove_quietly(path)


def recording_path_gone(path: Path) -> bool:
    return not Path(path).exists()


def flatten(chunks: Sequence[Sequence[float]]) -> list:
    """Expose chunk flattening for callers that only import this module."""
    return flatten_chunks(chunks)
