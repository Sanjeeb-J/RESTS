"""Local speech recognition with faster-whisper.

The model is loaded from the local cache only (``local_files_only=True``);
when the weights are missing the caller gets a clear message pointing at
``download_models.py`` instead of a silent multi-gigabyte download.

CUDA
----

``device`` accepts three values (see :data:`ASR_DEVICES`):

``auto`` (default)
    Probe with :func:`detect_device` and try CUDA first. Many Windows
    machines report a CUDA *device* (the driver is there) while the CUDA
    *runtime libraries* ``ctranslate2`` needs (``cublas64_12.dll`` and
    friends) are not installed. Because faster-whisper only touches cuBLAS
    on the first ``encode``, that failure can surface **while the lazy
    transcription generator is being consumed**, not at model creation.
    Both paths are handled: the recording is retried once on CPU
    (``int8``), the instance stays on CPU for later recordings and a
    :attr:`FasterWhisperAsr.notice` explains what happened.

``cpu``
    Never touch CUDA at all - works without any NVIDIA libraries.

``cuda``
    Force CUDA. When the runtime is missing the error message explains
    what to install (or how to switch back to CPU) instead of failing
    with a bare ``cublas64_12.dll`` loader error.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Tuple

from .models import (
    model_root,
    whisper_model_name,
)

#: Accepted values for the ``device`` argument (and ``RESTS_ASR_DEVICE``).
ASR_DEVICES: Tuple[str, ...] = ("auto", "cpu", "cuda")

#: Substrings that identify a *CUDA runtime / driver* problem as opposed to
#: a normal recognition failure (bad file, OOM, ...).
CUDA_ERROR_MARKERS: Tuple[str, ...] = (
    "cublas",
    "cudnn",
    "cudart",
    "cufft",
    "curand",
    "cusolver",
    "cusparse",
    "nvrtc",
    "ptxas",
    "libcudart",
    "cuda driver",
    "cuda_error",
    "no cuda-capable device",
    "cuda is not supported",
    ".dll is not found or cannot be loaded",
)


class AsrUnavailableError(RuntimeError):
    """Raised when the recogniser cannot be loaded (usually: not downloaded)."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language: Optional[str]
    ok: bool
    error: str = ""

    @classmethod
    def failure(cls, error: str) -> "Transcript":
        return cls(text="", language=None, ok=False, error=error)


class AsrEngine(Protocol):
    """Interface implemented by :class:`FasterWhisperAsr` and test fakes."""

    def transcribe(self, audio_path: str) -> Transcript:
        ...


def detect_device() -> Tuple[str, str]:
    """Prefer CUDA, fall back to CPU. Returns ``(device, compute_type)``."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def is_cuda_runtime_error(exc: BaseException) -> bool:
    """True when ``exc`` means "CUDA cannot be used here" (missing libs, ...)."""
    message = str(exc).lower()
    if any(marker in message for marker in CUDA_ERROR_MARKERS):
        return True
    return "cuda" in message and any(
        hint in message
        for hint in (".dll", "not found", "cannot be loaded", "not available", "not supported")
    )


#: The CUDA runtime DLLs ``ctranslate2`` loads lazily on the first encode.
CUDA_RUNTIME_DLLS: Tuple[str, ...] = ("cublas64_12.dll", "cudart64_12.dll")
#: cuDNN SONAMEs tried in order (the exact major version varies by install).
CUDA_DNN_CANDIDATES: Tuple[str, ...] = (
    "cudnn64_10.dll",
    "cudnn64_9.dll",
    "cudnn64_8.dll",
)


def _dll_loads(name: str) -> bool:
    if not sys.platform.startswith("win"):  # pragma: no cover - Windows only app
        return True
    import ctypes

    try:
        ctypes.WinDLL(name)
    except OSError:
        return False
    return True


def cuda_runtime_probe() -> "dict[str, bool]":
    """Best-effort check that the CUDA runtime DLLs ctranslate2 needs load.

    Returns an empty mapping on non-Windows platforms. Values are for
    reporting only: a missing DLL is *not* fatal because auto mode falls
    back to CPU.
    """
    probe = {name: _dll_loads(name) for name in CUDA_RUNTIME_DLLS}
    probe["cudnn64_*.dll"] = any(_dll_loads(name) for name in CUDA_DNN_CANDIDATES)
    return probe


def cuda_runtime_summary(probe: "dict[str, bool]") -> str:
    missing = [name for name, ok in probe.items() if not ok]
    if not missing:
        return "all present (" + ", ".join(probe) + ")"
    return "missing: " + ", ".join(missing)


def cuda_setup_message(exc: BaseException) -> str:
    """Actionable text for a forced-CUDA failure."""
    return (
        f"CUDA was requested but the runtime is unusable: {exc}. "
        "ctranslate2 needs the CUDA 12 runtime libraries (cublas64_12.dll, "
        "cudart64_12.dll, cudnn64_*.dll) reachable from PATH - install the "
        "NVIDIA CUDA 12.x toolkit plus a matching cuDNN, or run on CPU "
        "instead: --asr-device cpu, RESTS_ASR_DEVICE=cpu, or choose 'CPU' "
        "in the Device box (no CUDA installation required)."
    )


class FasterWhisperAsr:
    """Thin, lazily loaded wrapper around ``faster_whisper.WhisperModel``."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        *,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
        download_root: Optional[Path] = None,
        local_files_only: bool = True,
        model_factory=None,
    ) -> None:
        requested = (device or "auto").strip().lower()
        if requested not in ASR_DEVICES:
            raise ValueError(
                f"unknown ASR device {device!r}; expected one of {ASR_DEVICES}"
            )
        self.model_name = model_name or whisper_model_name()
        self.requested_device = requested
        self._compute_type_override = compute_type
        self.download_root = Path(download_root) if download_root else model_root()
        self.local_files_only = local_files_only
        self._model_factory = model_factory
        self._model = None
        #: Set once auto mode has fallen back to CPU; guards against a
        #: second retry loop (and explains the change to the user).
        self._fell_back = False
        self.notice = ""
        self.device, self.compute_type = self._initial_device()

    # -- device handling ---------------------------------------------------
    def _initial_device(self) -> Tuple[str, str]:
        if self.requested_device == "cpu":
            return "cpu", self._compute_type_override or "int8"
        if self.requested_device == "cuda":
            return "cuda", self._compute_type_override or "float16"
        if self._compute_type_override:
            device, _ = detect_device()
            return device, self._compute_type_override
        return detect_device()

    def set_device(self, device: str) -> None:
        """Switch the requested device (``auto`` / ``cpu`` / ``cuda``) at runtime."""
        choice = (device or "").strip().lower()
        if choice not in ASR_DEVICES:
            raise ValueError(
                f"unknown ASR device {device!r}; expected one of {ASR_DEVICES}"
            )
        self.requested_device = choice
        self._model = None
        self._fell_back = False
        self.notice = ""
        self.device, self.compute_type = self._initial_device()

    @property
    def fell_back_to_cpu(self) -> bool:
        return self._fell_back

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _maybe_fallback(self, exc: BaseException) -> bool:
        """Handle a CUDA runtime failure; ``True`` = retry once on CPU."""
        if not is_cuda_runtime_error(exc):
            return False
        if self.requested_device != "auto":
            # forced CUDA -> the caller reports the setup message;
            # forced CPU never produces CUDA errors in the first place.
            return False
        if self._fell_back or self.device == "cpu":
            return False  # already on CPU: never retry a second time
        self.device, self.compute_type = "cpu", "int8"
        self._fell_back = True
        self._model = None
        self.notice = (
            f"CUDA unavailable ({exc}); speech recognition switched to CPU "
            f"(int8) - later recordings use CPU too"
        )
        return True

    # -- loading -----------------------------------------------------------
    def _resolve_factory(self):
        factory = self._model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel as factory  # type: ignore
            except Exception as exc:
                raise AsrUnavailableError(
                    "faster-whisper is not installed; run host_app\\setup.ps1"
                ) from exc
        return factory

    def load(self) -> None:
        """Load the model into memory (idempotent, one CPU retry in auto)."""
        if self._model is not None:
            return
        factory = self._resolve_factory()
        last_exc: Optional[BaseException] = None
        for _attempt in range(2):  # CUDA attempt, then at most one CPU attempt
            try:
                self._model = factory(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(self.download_root),
                    local_files_only=self.local_files_only,
                )
                return
            except Exception as exc:
                last_exc = exc
                if self._maybe_fallback(exc):
                    continue
                raise AsrUnavailableError(self._unavailable_message(exc)) from exc
        raise AsrUnavailableError(self._unavailable_message(last_exc)) from last_exc

    def _unavailable_message(self, exc: Optional[BaseException]) -> str:
        if exc is None:  # pragma: no cover - defensive
            return f"speech model {self.model_name!r} could not be loaded"
        if is_cuda_runtime_error(exc):
            if self.requested_device == "cuda":
                return cuda_setup_message(exc)
            return (
                f"speech model {self.model_name!r} could not start on CUDA "
                f"({exc}); run host_app\\download_models.py if the weights "
                f"are missing, or select CPU (--asr-device cpu)"
            )
        return (
            f"speech model {self.model_name!r} is not available locally "
            f"({exc}); run host_app\\download_models.py"
        )

    # -- transcription -----------------------------------------------------
    def _transcribe_failure(self, exc: BaseException) -> str:
        if is_cuda_runtime_error(exc) and self.requested_device == "cuda":
            return cuda_setup_message(exc)
        return f"recognition failed: {exc}"

    def transcribe(self, audio_path: str) -> Transcript:
        try:
            self.load()
        except AsrUnavailableError as exc:
            return Transcript.failure(str(exc))
        last_exc: Optional[BaseException] = None
        for _attempt in range(2):  # CUDA attempt, then at most one CPU attempt
            try:
                segments, info = self._model.transcribe(
                    str(audio_path),
                    beam_size=1,
                    vad_filter=True,
                )
                # The generator is lazy: CUDA errors raised by the first
                # encode() surface here, while the text is being collected.
                text = "".join(segment.text for segment in segments).strip()
            except Exception as exc:
                last_exc = exc
                if self._maybe_fallback(exc):
                    try:
                        self.load()
                    except AsrUnavailableError as cpu_exc:
                        return Transcript.failure(str(cpu_exc))
                    continue
                return Transcript.failure(self._transcribe_failure(exc))
            language = getattr(info, "language", None)
            if not text:
                return Transcript(text="", language=language, ok=True, error="")
            return Transcript(text=text, language=language, ok=True)
        if last_exc is not None:  # pragma: no cover - loop always returns first
            return Transcript.failure(self._transcribe_failure(last_exc))
        return Transcript.failure("recognition failed")  # pragma: no cover

    # -- reporting ---------------------------------------------------------
    def status(self) -> str:
        """Short human readable status for the UI / self-check."""
        if self._model is not None:
            base = f"loaded ({self.model_name}, {self.device})"
        else:
            marker = "present" if self._model_exists() else "MISSING"
            base = f"{self.model_name} [{marker}] on {self.device} ({self.compute_type})"
        if self.requested_device != "auto":
            base += f", device={self.requested_device}"
        if self.notice:
            base += f" | {self.notice}"
        return base

    def _model_exists(self) -> bool:
        root = self.download_root
        if not root.exists():
            return False
        needle = self.model_name.replace("/", "--")
        for path in root.rglob("*"):
            if needle in path.name:
                return True
        return False
