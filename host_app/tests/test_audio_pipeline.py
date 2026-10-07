"""Audio capture + temporary-file handling with fake devices."""

from __future__ import annotations

import wave

import pytest

from conftest import FakeAsr, FakeMicrophoneStream
from rests_host.asr import Transcript
from rests_host.audio import (
    MicrophoneRecorder,
    RecorderError,
    flatten_chunks,
    is_long_enough,
    write_wav,
)
from rests_host.languages import language_by_key
from rests_host.pipeline import process_recording
from rests_host.translate import PassthroughBackend, TranslationService


def make_recorder(blocks=None):
    holder = {}

    def factory(callback, **kwargs):
        stream = FakeMicrophoneStream(callback, blocks=blocks, **kwargs)
        holder["stream"] = stream
        return stream

    return MicrophoneRecorder(stream_factory=factory), holder


def test_recorder_captures_from_fake_device():
    recorder, holder = make_recorder(blocks=[[0.0, 0.5, -0.5], [0.25, 0.75]])
    assert recorder.is_recording is False
    recorder.start()
    assert recorder.is_recording is True
    holder["stream"].pump()
    samples = recorder.stop()
    assert samples == [0.0, 0.5, -0.5, 0.25, 0.75]
    assert recorder.is_recording is False
    assert holder["stream"].stopped and holder["stream"].closed


def test_recorder_rejects_double_start():
    recorder, _ = make_recorder()
    recorder.start()
    with pytest.raises(RecorderError, match="already in progress"):
        recorder.start()
    recorder.discard()
    assert recorder.is_recording is False


def test_recorder_stop_without_start_returns_empty():
    recorder, _ = make_recorder()
    assert recorder.stop() == []


def test_recorder_reports_broken_device():
    def broken(**_kwargs):
        raise OSError("no audio device")

    recorder = MicrophoneRecorder(stream_factory=lambda *a, **k: broken(**k))
    with pytest.raises(RecorderError, match="could not open the microphone"):
        recorder.start()


def test_min_length_gate():
    assert is_long_enough(16_000, 16_000) is True       # 1.0 s
    assert is_long_enough(3_200, 16_000) is False       # 0.2 s
    assert is_long_enough(4_800, 16_000) is True        # 0.3 s
    assert is_long_enough(1, 0) is False


def test_flatten_chunks_downmixes_stereo():
    chunks = [[[1.0, 0.0], [0.0, 1.0]], [[0.5, 0.5]]]
    assert flatten_chunks(chunks) == [0.5, 0.5, 0.5]


def test_write_wav_roundtrip(tmp_path):
    path = tmp_path / "tone.wav"
    samples = [0.0, 0.5, -0.5, 1.0] * 100
    write_wav(path, samples, 16_000)
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        assert handle.getnframes() == len(samples)
        frames = handle.readframes(handle.getnframes())
    assert len(frames) == len(samples) * 2


def test_pipeline_removes_temp_recording(tmp_path):
    asr = FakeAsr(text="hello", language="en")
    service = TranslationService(PassthroughBackend())
    samples = [0.1] * 8_000

    result = process_recording(
        samples,
        asr,
        service,
        language_by_key("auto"),
        language_by_key("ml"),
        sample_rate=16_000,
        tmp_dir=tmp_path,
    )

    assert result.ok
    assert result.source_text == "hello"
    assert result.detected_language == "en"
    assert asr.seen_path is not None
    assert asr.existed_during_call is True          # the file really existed
    assert list(tmp_path.glob("*.wav")) == []       # ...and is gone afterwards
    assert asr.calls == 1


def test_pipeline_removes_temp_recording_when_asr_raises(tmp_path):
    asr = FakeAsr(exc=RuntimeError("model exploded"))
    service = TranslationService(PassthroughBackend())

    result = process_recording(
        [0.1] * 1_000,
        asr,
        service,
        language_by_key("en"),
        language_by_key("en"),
        tmp_dir=tmp_path,
    )

    assert result.ok is False
    assert "model exploded" in result.error
    assert list(tmp_path.glob("*.wav")) == []


def test_pipeline_keeps_source_caption_when_translation_unavailable(tmp_path):
    class ExplodingBackend(PassthroughBackend):
        name = "exploding"

        def translate(self, text, source, target):
            raise RuntimeError("no model on this machine")

    asr = FakeAsr(text="hello world", language="en")
    service = TranslationService(ExplodingBackend())

    result = process_recording(
        [0.1] * 1_000,
        asr,
        service,
        language_by_key("auto"),
        language_by_key("hi"),
        tmp_dir=tmp_path,
    )

    assert result.ok is True                     # transcription worked
    assert result.translation_ok is False
    assert "no model on this machine" in result.translation_error
    assert result.source_text == "hello world"
    assert result.translated_text == "hello world"  # captions keep working
    assert list(tmp_path.glob("*.wav")) == []


def test_pipeline_reports_asr_failure(tmp_path):
    asr = FakeAsr(ok=False, error="speech model 'x' is not available locally")
    service = TranslationService(PassthroughBackend())

    result = process_recording(
        [0.1] * 1_000,
        asr,
        service,
        language_by_key("en"),
        language_by_key("en"),
        tmp_dir=tmp_path,
    )

    assert result.ok is False
    assert "not available locally" in result.error
    assert result.source_text == ""
    assert list(tmp_path.glob("*.wav")) == []


def test_pipeline_records_duration_and_keys(tmp_path):
    asr = FakeAsr(text="hi", language="ja")
    service = TranslationService(PassthroughBackend())

    result = process_recording(
        [0.0] * 32_000,  # 2 s at 16 kHz
        asr,
        service,
        language_by_key("auto"),
        language_by_key("ko"),
        sample_rate=16_000,
        tmp_dir=tmp_path,
    )

    assert result.ok
    assert result.duration_seconds == pytest.approx(2.0, abs=0.01)
    assert result.language_key == "ja"
    assert result.target_key == "ko"
