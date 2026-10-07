"""Runtime configuration for the RESTS host application."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .asr import ASR_DEVICES
from .models import (
    ENV_TRANSLATION_BACKEND,
    DEFAULT_NLLB_MODEL,
    DEFAULT_WHISPER_MODEL,
)
from .uart import DEFAULT_HOST, DEFAULT_PORT

ENV_UART_HOST = "RESTS_UART_HOST"
ENV_UART_PORT = "RESTS_UART_PORT"
ENV_PTT_KEY = "RESTS_PTT_KEY"
ENV_SPEECH = "RESTS_SPEECH"
ENV_ASR_DEVICE = "RESTS_ASR_DEVICE"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def _env_choice(name: str, default: str, choices: tuple) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in choices:
        raise ValueError(
            f"{name}={raw!r} is invalid; expected one of {', '.join(choices)}"
        )
    return value


@dataclass
class AppConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    ptt_key: str = "F9"
    speech_enabled: bool = True
    backend: str = "nllb"
    whisper_model: str = DEFAULT_WHISPER_MODEL
    nllb_model: str = DEFAULT_NLLB_MODEL
    sample_rate: int = 16_000
    #: ``auto`` / ``cpu`` / ``cuda`` - see :mod:`rests_host.asr`.
    asr_device: str = "auto"

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            host=os.environ.get(ENV_UART_HOST, DEFAULT_HOST),
            port=int(os.environ.get(ENV_UART_PORT, str(DEFAULT_PORT))),
            ptt_key=os.environ.get(ENV_PTT_KEY, "F9"),
            speech_enabled=_env_flag(ENV_SPEECH, True),
            backend=os.environ.get(ENV_TRANSLATION_BACKEND, "nllb"),
            asr_device=_env_choice(ENV_ASR_DEVICE, "auto", ASR_DEVICES),
        )
