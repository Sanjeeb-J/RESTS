"""CUDA detection, automatic CPU fallback and the explicit device modes.

Everything here runs against fake model factories - no models, no GPU and
no network - but the failure strings are the real ones produced by
ctranslate2/faster-whisper on a machine whose CUDA runtime is missing.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rests_host.asr import (
    ASR_DEVICES,
    AsrUnavailableError,
    FasterWhisperAsr,
    Transcript,
    cuda_runtime_probe,
    cuda_setup_message,
    detect_device,
    is_cuda_runtime_error,
)
from rests_host.config import AppConfig, ENV_ASR_DEVICE

CUBLAS_ERROR = "Library cublas64_12.dll is not found or cannot be loaded"


class _RaisingSegments:
    """Stands in for the lazy segment generator of faster-whisper."""

    def __init__(self, message: str):
        self._message = message

    def __iter__(self):
        raise RuntimeError(self._message)


class FakeModel:
    def __init__(self, device: str, generation_error: str = ""):
        self.device = device
        self.generation_error = generation_error
        self.transcribe_calls = 0

    def transcribe(self, path, *, beam_size=1, vad_filter=True):
        self.transcribe_calls += 1
        if self.generation_error:
            return (
                _RaisingSegments(self.generation_error),
                SimpleNamespace(language="en"),
            )
        return ([SimpleNamespace(text=" hello")], SimpleNamespace(language="en"))


def make_factory(load_errors=None, generation_errors=None):
    """Factory with a scripted CUDA failure on the first attempt.

    ``load_errors`` entries are exceptions to raise (``None`` = success);
    ``generation_errors`` are strings raised lazily while the segment
    generator is consumed.
    """
    load_errors = list(load_errors or [])
    generation_errors = list(generation_errors or [])
    calls = []

    def factory(name, *, device, compute_type, download_root, local_files_only):
        calls.append(
            {
                "name": name,
                "device": device,
                "compute_type": compute_type,
                "local_files_only": local_files_only,
            }
        )
        if load_errors:
            error = load_errors.pop(0)
            if error is not None:
                raise error
        generation = generation_errors.pop(0) if generation_errors else ""
        return FakeModel(device, generation_error=generation)

    factory.calls = calls
    return factory


@pytest.fixture
def cuda_detected(monkeypatch):
    """Pretend the machine has a CUDA device (the runtime may still be broken)."""
    monkeypatch.setattr(
        "rests_host.asr.detect_device", lambda: ("cuda", "float16")
    )
    return "cuda"


# -- error classification ---------------------------------------------------
@pytest.mark.parametrize(
    "message",
    [
        CUBLAS_ERROR,
        "Unable to load libcudart.so.12",
        "cudnn64_8.dll is not found or cannot be loaded",
        "no cuda-capable device detected",
        "CUDA driver version is insufficient for CUDA runtime version",
        "Library nvcuda.dll not found or cannot be loaded",
    ],
)
def test_cuda_runtime_errors_are_recognised(message):
    assert is_cuda_runtime_error(RuntimeError(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "recognition failed: bad audio",
        "model 'x' is not available locally",
        "CUDA out of memory",
        "value 1234 is not a valid language",
    ],
)
def test_ordinary_errors_are_not_cuda_errors(message):
    assert is_cuda_runtime_error(RuntimeError(message)) is False


def test_cuda_setup_message_tells_the_user_what_to_do():
    message = cuda_setup_message(RuntimeError(CUBLAS_ERROR))
    assert CUBLAS_ERROR in message
    assert "cublas64_12.dll" in message
    assert "--asr-device cpu" in message
    assert "RESTS_ASR_DEVICE=cpu" in message


def test_cuda_runtime_probe_reports_the_runtime_dlls():
    probe = cuda_runtime_probe()
    assert isinstance(probe, dict)
    if probe:  # Windows only
        assert "cublas64_12.dll" in probe
        assert "cudart64_12.dll" in probe
        assert "cudnn64_*.dll" in probe


def test_detect_device_returns_a_known_pair():
    device, compute = detect_device()
    assert device in ("cuda", "cpu")
    assert compute in ("float16", "int8")


# -- device modes -----------------------------------------------------------
def test_device_modes_are_documented():
    assert ASR_DEVICES == ("auto", "cpu", "cuda")


def test_cpu_mode_never_calls_cuda(tmp_path):
    factory = make_factory()
    asr = FasterWhisperAsr(
        device="cpu", download_root=tmp_path, model_factory=factory
    )
    assert asr.device == "cpu" and asr.compute_type == "int8"
    transcript = asr.transcribe("missing.wav")
    assert transcript.ok
    assert [call["device"] for call in factory.calls] == ["cpu"]
    assert factory.calls[0]["compute_type"] == "int8"
    assert factory.calls[0]["local_files_only"] is True
    assert asr.notice == ""


def test_auto_mode_loads_on_the_detected_device(tmp_path):
    factory = make_factory()
    asr = FasterWhisperAsr(download_root=tmp_path, model_factory=factory)
    assert asr.requested_device == "auto"
    asr.load()
    assert [call["device"] for call in factory.calls] == [asr.device]


def test_missing_weights_report_the_download_script(tmp_path):
    def broken(name, **kwargs):
        raise RuntimeError("no such file or directory: model.bin")

    asr = FasterWhisperAsr(
        device="cpu", download_root=tmp_path, model_factory=broken
    )
    with pytest.raises(AsrUnavailableError, match="download_models.py"):
        asr.load()
    transcript = asr.transcribe("x.wav")
    assert transcript.ok is False
    assert "download_models.py" in transcript.error


# -- automatic CPU fallback -------------------------------------------------
def test_creation_time_cuda_failure_falls_back_to_cpu(tmp_path, cuda_detected):
    factory = make_factory(load_errors=[RuntimeError(CUBLAS_ERROR), None])
    asr = FasterWhisperAsr(download_root=tmp_path, model_factory=factory)
    assert asr.device == cuda_detected and asr.compute_type == "float16"
    asr.load()
    assert [call["device"] for call in factory.calls] == ["cuda", "cpu"]
    assert factory.calls[-1]["compute_type"] == "int8"
    assert asr.device == "cpu"
    assert asr.fell_back_to_cpu is True
    assert "CUDA unavailable" in asr.notice
    assert "cublas64_12.dll" in asr.notice
    assert "CPU" in asr.status()
    assert len(factory.calls) == 2  # exactly one retry


def test_lazy_generation_failure_falls_back_and_stays_on_cpu(tmp_path, cuda_detected):
    factory = make_factory(generation_errors=[CUBLAS_ERROR])
    asr = FasterWhisperAsr(download_root=tmp_path, model_factory=factory)
    transcript = asr.transcribe("audio.wav")

    assert transcript.ok is True
    assert transcript.text == "hello"
    assert transcript.language == "en"
    assert asr.fell_back_to_cpu is True
    assert "CUDA unavailable" in asr.notice
    # first attempt on the detected device, retry on CPU
    assert [call["device"] for call in factory.calls] == ["cuda", "cpu"]

    # the instance stays on CPU: no third attempt, no repeated notice
    loads = len(factory.calls)
    again = asr.transcribe("audio.wav")
    assert again.ok
    assert len(factory.calls) == loads
    assert "cublas64_12.dll" in asr.notice


def test_non_cuda_failure_is_reported_without_retry(tmp_path):
    factory = make_factory(load_errors=[RuntimeError("model.bin is corrupt")])
    asr = FasterWhisperAsr(device="cpu", download_root=tmp_path, model_factory=factory)
    with pytest.raises(AsrUnavailableError, match="download_models.py"):
        asr.load()
    assert len(factory.calls) == 1
    assert asr.notice == ""


def test_transcription_failure_without_fallback_keeps_the_error(tmp_path):
    calls = []

    def factory(name, **kwargs):
        calls.append(kwargs.get("device"))
        raise RuntimeError(CUBLAS_ERROR)

    asr = FasterWhisperAsr(device="cuda", download_root=tmp_path, model_factory=factory)
    transcript = asr.transcribe("audio.wav")
    assert transcript.ok is False
    assert "CUDA was requested" in transcript.error
    assert "--asr-device cpu" in transcript.error
    assert calls == ["cuda"]  # forced CUDA never silently retries
    assert asr.fell_back_to_cpu is False


def test_forced_cuda_setup_error_is_actionable(tmp_path):
    def factory(name, **kwargs):
        raise RuntimeError(CUBLAS_ERROR)

    asr = FasterWhisperAsr(device="cuda", download_root=tmp_path, model_factory=factory)
    with pytest.raises(AsrUnavailableError) as excinfo:
        asr.load()
    assert "CUDA was requested" in str(excinfo.value)
    assert "cudart64_12.dll" in str(excinfo.value)
    assert asr.device == "cuda"


def test_set_device_switches_and_resets(tmp_path):
    factory = make_factory()
    asr = FasterWhisperAsr(device="cuda", download_root=tmp_path, model_factory=factory)
    asr.load()
    assert factory.calls[-1]["device"] == "cuda"

    asr.set_device("cpu")
    assert asr.requested_device == "cpu"
    assert asr.device == "cpu"
    assert asr.is_loaded is False
    asr.load()
    assert factory.calls[-1]["device"] == "cpu"

    asr.set_device("auto")
    assert asr.requested_device == "auto"
    with pytest.raises(ValueError, match="unknown ASR device"):
        asr.set_device("gpu")


def test_unknown_device_is_rejected_at_construction(tmp_path):
    with pytest.raises(ValueError, match="unknown ASR device"):
        FasterWhisperAsr(device="gpu", download_root=tmp_path)


# -- configuration ----------------------------------------------------------
def test_config_reads_the_device_from_the_environment(monkeypatch):
    monkeypatch.setenv(ENV_ASR_DEVICE, "CPU")
    assert AppConfig.from_env().asr_device == "cpu"
    monkeypatch.delenv(ENV_ASR_DEVICE, raising=False)
    assert AppConfig.from_env().asr_device == "auto"
    monkeypatch.setenv(ENV_ASR_DEVICE, "gpu")
    with pytest.raises(ValueError, match="RESTS_ASR_DEVICE"):
        AppConfig.from_env()


def test_command_line_offers_the_device_modes():
    from rests_host.__main__ import build_parser

    args = build_parser().parse_args(["--asr-device", "cpu"])
    assert args.asr_device == "cpu"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--asr-device", "gpu"])


# -- pipeline plumbing ------------------------------------------------------
def test_pipeline_propagates_backend_notices(tmp_path):
    from rests_host.languages import language_by_key
    from rests_host.pipeline import process_recording
    from rests_host.translate import PassthroughBackend, TranslationService

    class NoticingAsr:
        notice = "CUDA unavailable; speech recognition switched to CPU (int8)"

        def transcribe(self, audio_path):
            return Transcript(text="hi", language="en", ok=True)

    result = process_recording(
        [0.1] * 1_000,
        NoticingAsr(),
        TranslationService(PassthroughBackend()),
        language_by_key("auto"),
        language_by_key("ml"),
        sample_rate=16_000,
        tmp_dir=tmp_path,
    )
    assert result.ok
    assert "switched to CPU" in result.asr_notice
