"""Microphone capture and WAV helpers.

Recording uses ``sounddevice``'s callback (PortAudio) so audio arrives on a
background audio thread while the Tk event loop stays responsive.  The
stream implementation is injectable, which lets the tests drive the whole
PTT path with a fake microphone device.
"""

from __future__ import annotations

import os
import struct
import threading
import time
import wave
from pathlib import Path
from typing import Callable, List, Optional, Sequence

DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_CHANNELS = 1
DEFAULT_DTYPE = "float32"

#: Streams shorter than this are treated as accidental key taps.
MIN_RECORDING_SECONDS = 0.3

StreamFactory = Callable[..., object]


class RecorderError(RuntimeError):
    """Raised when the microphone cannot be used."""


def _default_stream_factory(callback, **kwargs):
    # Imported lazily: the test-suite must not require PortAudio.
    try:
        import sounddevice
    except Exception as exc:  # pragma: no cover - depends on host machine
        raise RecorderError(
            "sounddevice/PortAudio is not available; run setup.ps1 first"
        ) from exc
    stream = sounddevice.InputStream(callback=callback, **kwargs)
    return stream


class MicrophoneRecorder:
    """Hold-to-record microphone capture."""

    def __init__(
        self,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        dtype: str = DEFAULT_DTYPE,
        *,
        stream_factory: StreamFactory = _default_stream_factory,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self._stream_factory = stream_factory
        self._chunks: List[Sequence[float]] = []
        self._stream = None
        self._lock = threading.Lock()
        self._started_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def elapsed(self) -> float:
        if self._stream is None:
            return 0.0
        return time.monotonic() - self._started_at

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise RecorderError("recording already in progress")
            self._chunks = []

        def _callback(indata, _frames, _time_info, _status) -> None:
            with self._lock:
                try:
                    self._chunks.append(indata.copy())
                except AttributeError:  # pragma: no cover - plain sequences
                    self._chunks.append(list(indata))

        try:
            stream = self._stream_factory(
                _callback,
                channels=self.channels,
                samplerate=self.sample_rate,
                dtype=self.dtype,
                blocksize=0,
            )
            if hasattr(stream, "start"):
                stream.start()
        except RecorderError:
            raise
        except Exception as exc:
            raise RecorderError(f"could not open the microphone: {exc}") from exc

        with self._lock:
            self._stream = stream
            self._started_at = time.monotonic()

    def stop(self) -> List[float]:
        """Stop capture and return the recorded mono samples in [-1, 1]."""
        with self._lock:
            stream, self._stream = self._stream, None
            chunks, self._chunks = self._chunks, []
        if stream is None:
            return []
        try:
            if hasattr(stream, "stop"):
                stream.stop()
            if hasattr(stream, "close"):
                stream.close()
        except Exception:  # pragma: no cover - device unplugged mid-recording
            pass
        return flatten_chunks(chunks)

    def discard(self) -> None:
        """Stop capture and drop the audio without returning it."""
        with self._lock:
            stream, self._stream = self._stream, None
            self._chunks = []
        if stream is not None:
            try:
                if hasattr(stream, "stop"):
                    stream.stop()
                if hasattr(stream, "close"):
                    stream.close()
            except Exception:  # pragma: no cover
                pass


def flatten_chunks(chunks: Sequence[Sequence[float]]) -> List[float]:
    """Concatenate recorded blocks and down-mix multi-channel audio."""
    samples: List[float] = []
    for chunk in chunks:
        values = _chunk_to_list(chunk)
        if values and isinstance(values[0], (list, tuple)):
            for frame in values:
                samples.append(sum(frame) / len(frame))
        else:
            samples.extend(values)
    return samples


def _chunk_to_list(chunk) -> List:
    if hasattr(chunk, "tolist"):
        return chunk.tolist()
    return list(chunk)


def write_wav(path: Path, samples: Sequence[float], sample_rate: int) -> Path:
    """Write mono 16-bit PCM samples to ``path`` using the stdlib only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = _to_pcm16(samples)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm)
    return path


def _to_pcm16(samples: Sequence[float]) -> bytes:
    if hasattr(samples, "astype"):  # numpy fast path
        try:
            import numpy as np

            array = np.asarray(samples, dtype=np.float64)
            array = np.clip(array * 32767.0, -32768, 32767).astype("<i2")
            return array.tobytes()
        except Exception:  # pragma: no cover - fall through to the slow path
            pass
    out = bytearray()
    for sample in samples:
        value = int(float(sample) * 32767.0)
        if value > 32767:
            value = 32767
        elif value < -32768:
            value = -32768
        out += struct.pack("<h", value)
    return bytes(out)


def recording_seconds(sample_count: int, sample_rate: int) -> float:
    if sample_rate <= 0:
        return 0.0
    return sample_count / float(sample_rate)


def is_long_enough(sample_count: int, sample_rate: int) -> bool:
    return recording_seconds(sample_count, sample_rate) >= MIN_RECORDING_SECONDS


def temp_recording_dir(base: Optional[Path] = None) -> Path:
    """Directory for short-lived recordings (removed from Git)."""
    if base is None:
        base = Path(__file__).resolve().parents[1] / "tmp"
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    return base


def remove_quietly(path: Path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:  # pragma: no cover - best effort cleanup
        pass
