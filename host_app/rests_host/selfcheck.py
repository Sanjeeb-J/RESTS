"""Command line self-check: verifies hardware/model prerequisites.

``python -m rests_host --self-check`` never opens the UI and never
downloads anything, so it is safe to run on an air-gapped machine.
"""

from __future__ import annotations

import platform
import sys
import threading
from typing import List, Optional

from .asr import (
    FasterWhisperAsr,
    cuda_runtime_probe,
    cuda_runtime_summary,
    detect_device,
)
from .config import AppConfig
from .languages import (
    BASE_LANGUAGES,
    OMITTED_LANGUAGES,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
)
from .models import model_root
from .translate import create_backend
from .tts import WindowsSpeech
from .uart import ConnectionState, ReplyEvent, RenodeUart, StateEvent


def _line(label: str, value: str) -> str:
    return f"  {label:<22} {value}"


def _asr_device_line(requested: str, detected: str, runtime: dict) -> str:
    """One line describing which device the recogniser will actually use."""
    runtime_broken = bool(runtime) and not all(runtime.values())
    if requested == "cpu":
        return "cpu (int8) - forced, CUDA is never touched"
    if requested == "cuda":
        text = "cuda (float16) - forced"
        if detected != "cuda" or runtime_broken:
            text += (
                "; WARNING: CUDA is unusable here, recordings will fail - "
                "use --asr-device cpu (or RESTS_ASR_DEVICE=cpu)"
            )
        return text
    if detected == "cuda" and runtime_broken:
        return (
            "auto - CUDA device detected but its runtime DLLs are missing: "
            "recordings fall back to CPU (int8)"
        )
    if detected == "cuda":
        return "auto - CUDA first (float16), automatic CPU (int8) fallback"
    return "auto - CUDA not detected: CPU (int8)"


def _preview(labels: List[str], limit: int = 6) -> str:
    if len(labels) <= limit:
        return ", ".join(labels)
    return ", ".join(labels[:limit]) + f", ... +{len(labels) - limit}"


def collect_report(config: AppConfig, *, probe_uart: bool = False) -> List[str]:
    lines: List[str] = []
    lines.append("RESTS host application self-check")
    lines.append("=" * 60)
    lines.append(_line("python", platform.python_version()))
    lines.append(_line("platform", platform.platform()))
    lines.append(_line("model directory", str(model_root())))

    device, compute = detect_device()
    lines.append(_line("compute device", f"detected {device} ({compute})"))
    runtime = cuda_runtime_probe()
    if runtime:
        lines.append(_line("cuda runtime", cuda_runtime_summary(runtime)))
    lines.append(_line("asr device", _asr_device_line(config.asr_device, device, runtime)))

    asr = FasterWhisperAsr(device=config.asr_device)
    lines.append(_line("speech model", asr.status()))

    backend = create_backend(config.backend)
    usable, detail = backend.available()
    lines.append(_line("translation backend", f"{backend.name}: {'ready' if usable else 'unavailable'} ({detail})"))
    if backend.name == "nllb":
        lines.append(_line("translation licence", "NLLB-200 = CC-BY-NC 4.0 (research / non-commercial)"))

    try:
        import sounddevice  # noqa: F401

        lines.append(_line("microphone", "sounddevice/PortAudio available"))
    except Exception as exc:
        lines.append(_line("microphone", f"unavailable ({exc})"))

    lines.append(
        _line(
            "languages",
            f"{len(BASE_LANGUAGES)} targets "
            f"(Whisper alphabet & NLLB vocabulary); "
            f"source: Auto + {len(BASE_LANGUAGES)}",
        )
    )
    lines.append(
        _line("no NLLB tag", _preview([f"{key} {label}" for key, label in OMITTED_LANGUAGES]))
    )

    speech = WindowsSpeech()
    voices = speech.voices()
    lines.append(_line("windows voices", f"{len(voices)} installed"))
    spoken = [lang.label for lang in TARGET_LANGUAGES if speech.status(lang).available]
    silent = [lang.label for lang in TARGET_LANGUAGES if not speech.status(lang).available]
    lines.append(
        _line(
            "spoken targets",
            f"{len(spoken)} of {len(TARGET_LANGUAGES)}: {_preview(spoken) or 'none'}",
        )
    )
    if silent:
        lines.append(
            _line(
                "captions only",
                f"{len(silent)} targets (e.g. {_preview(silent)})",
            )
        )
    lines.append(
        _line(
            "source choices",
            f"{len(SOURCE_LANGUAGES)} (Auto + {len(BASE_LANGUAGES)}): "
            + _preview([lang.label for lang in SOURCE_LANGUAGES]),
        )
    )
    lines.append(
        _line(
            "target choices",
            f"{len(TARGET_LANGUAGES)}: "
            + _preview([lang.label for lang in TARGET_LANGUAGES]),
        )
    )

    if probe_uart:
        lines.append(_line("uart probe", _probe_uart(config)))
    else:
        lines.append(_line("uart probe", f"skipped (use --probe-uart for {config.host}:{config.port})"))
    return lines


def _probe_uart(config: AppConfig, timeout: float = 5.0) -> str:
    replies: List[str] = []
    ready = threading.Event()

    def on_reply(event: ReplyEvent) -> None:
        replies.append(event.line)
        if event.line.startswith("RESTS/1"):
            ready.set()

    def on_state(event: StateEvent) -> None:
        if event.state is ConnectionState.READY:
            ready.set()

    uart = RenodeUart(
        config.host,
        config.port,
        on_state=on_state,
        on_reply=on_reply,
        handshake_timeout=timeout,
    )
    try:
        if not uart.connect():
            return f"FAILED ({uart.state_detail})"
        uart.send_command("PING")
        deadline = threading.Event()
        for _ in range(int(timeout * 20)):
            if any(line == "PONG" for line in replies):
                break
            deadline.wait(0.05)
        if "PONG" in replies:
            return "handshake + PING/PONG OK"
        return f"handshake OK, no PONG (replies: {replies or 'none'})"
    finally:
        uart.close()


def run_self_check(config: Optional[AppConfig] = None, *, probe_uart: bool = False) -> int:
    cfg = config or AppConfig.from_env()
    for line in collect_report(cfg, probe_uart=probe_uart):
        print(line)
    return 0


def main(argv: Optional[List[str]] = None) -> int:  # pragma: no cover - thin wrapper
    return run_self_check(probe_uart="--probe-uart" in (argv or sys.argv[1:]))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
