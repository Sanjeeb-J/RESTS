#!/usr/bin/env python3
"""Explicit download step for the local ML weights.

The application itself **never** downloads anything (all loads use
``local_files_only=True``); this script is the single, intentional place
where multi-gigabyte checkpoints are fetched, so you can run it once on a
machine with internet access and afterwards work fully offline.

Usage (from the repository root)::

    host_app\\.venv\\Scripts\\python.exe host_app\\download_models.py
    host_app\\.venv\\Scripts\\python.exe host_app\\download_models.py --skip-nllb

Licence reminder: ``facebook/nllb-200-distilled-600M`` is CC-BY-NC 4.0
(research / non-commercial only) - see ``host_app/README.md``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

if __package__ in (None, ""):  # allow "python download_models.py"
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from rests_host.models import (  # noqa: E402
    DEFAULT_NLLB_MODEL,
    DEFAULT_WHISPER_MODEL,
    model_root,
    nllb_model_name,
    whisper_model_name,
)


def _dir_size(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"  # pragma: no cover


def download_whisper(model_name: str, root: Path) -> None:
    print(f"[whisper] downloading {model_name} into {root} ...")
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:
        raise SystemExit(
            f"faster-whisper is not installed ({exc}); run host_app\\setup.ps1 first"
        )
    model = WhisperModel(
        model_name,
        device="cpu",
        compute_type="int8",
        download_root=str(root),
        local_files_only=False,
    )
    del model
    print(f"[whisper] {model_name} ready")


def download_nllb(model_name: str, root: Path) -> None:
    print(f"[nllb] downloading {model_name} into {root} ...")
    print(
        "[nllb] NOTE: NLLB-200 weights are CC-BY-NC 4.0 - "
        "research / non-commercial use only."
    )
    try:
        from huggingface_hub import snapshot_download
    except Exception as exc:
        raise SystemExit(
            f"huggingface_hub is not installed ({exc}); run host_app\\setup.ps1 first"
        )
    snapshot_download(repo_id=model_name, cache_dir=str(root))
    print(f"[nllb] {model_name} ready")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--whisper-model", default=whisper_model_name())
    parser.add_argument("--nllb-model", default=nllb_model_name())
    parser.add_argument("--model-dir", default=None, help="override the model directory")
    parser.add_argument("--skip-whisper", action="store_true")
    parser.add_argument("--skip-nllb", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.model_dir).resolve() if args.model_dir else model_root()
    root.mkdir(parents=True, exist_ok=True)

    if not args.skip_whisper:
        download_whisper(args.whisper_model, root)
    if not args.skip_nllb:
        download_nllb(args.nllb_model, root)

    print(f"\nModel directory : {root}")
    print(f"Total size      : {_human(_dir_size(root))}")
    print("You can now run the application fully offline:")
    print("  host_app\\run.ps1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
