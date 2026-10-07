"""``python -m rests_host`` entry point."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from .asr import ASR_DEVICES
from .config import AppConfig


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rests_host",
        description="RESTS host application (local ASR + translation + UART control).",
    )
    parser.add_argument(
        "--self-check",
        action="store_true",
        help="verify models/voices/UART configuration without opening the UI",
    )
    parser.add_argument(
        "--probe-uart",
        action="store_true",
        help="with --self-check: perform a live HELLO?/PING handshake",
    )
    parser.add_argument("--host", help="override the Renode socket host")
    parser.add_argument("--port", type=int, help="override the Renode socket port")
    parser.add_argument(
        "--backend",
        choices=("nllb", "passthrough"),
        help="translation backend (default: nllb, or RESTS_TRANSLATION_BACKEND)",
    )
    parser.add_argument(
        "--asr-device",
        choices=ASR_DEVICES,
        help="speech recognition device: auto (CUDA with CPU fallback), "
        "cpu (never touch CUDA) or cuda (fail loudly when the CUDA "
        "runtime is missing); default: auto, or RESTS_ASR_DEVICE",
    )
    parser.add_argument(
        "--no-connect",
        action="store_true",
        help="start the UI without trying to connect to Renode automatically",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = AppConfig.from_env()
    except ValueError as exc:  # e.g. RESTS_ASR_DEVICE=gpu
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return 2
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.backend:
        config.backend = args.backend
    if args.asr_device:
        config.asr_device = args.asr_device

    if args.self_check:
        from .selfcheck import run_self_check

        return run_self_check(config, probe_uart=args.probe_uart)

    from .app import run

    run(config, auto_connect=not args.no_connect)
    return 0


if __name__ == "__main__":
    sys.exit(main())
