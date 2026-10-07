"""Local translation backends.

The default backend is `facebook/nllb-200-distilled-600M
<https://huggingface.co/facebook/nllb-200-distilled-600M>`_ and it is
**not** part of the permissive stack around it:

.. admonition:: Licence (read this)
   :class: warning

   NLLB-200 distilled weights are published by Meta under the
   **Creative Commons Attribution-NonCommercial 4.0 (CC-BY-NC 4.0)**
   licence, i.e. research / non-commercial use only.  They must not be
   used for commercial products or services.  The same applies to the
   translated output derived from them.  The ``transformers`` code that
   loads them is Apache-2.0 and unrelated to this restriction.

The backend is deliberately behind the :class:`TranslationBackend`
interface so it can be swapped (``RESTS_TRANSLATION_BACKEND=passthrough``
for a no-model run, or a future backend for a permissively licensed
model) without touching the UI or the pipeline.
"""

from __future__ import annotations

import abc
import os
from dataclasses import dataclass
from typing import Optional

from .asr import is_cuda_runtime_error
from .languages import Language, resolve_source_language
from .models import ENV_TRANSLATION_BACKEND, nllb_model_name, model_root

BACKEND_ENV_CHOICES = ("nllb", "passthrough")


class TranslationError(RuntimeError):
    """Raised when a backend cannot produce a translation."""


@dataclass(frozen=True)
class TranslationResult:
    text: str
    ok: bool
    error: str = ""
    backend: str = ""
    source_key: str = ""
    target_key: str = ""


class TranslationBackend(abc.ABC):
    """Replaceable translation service contract."""

    name: str = "abstract"

    @abc.abstractmethod
    def translate(self, text: str, source: Language, target: Language) -> str:
        """Translate ``text`` from ``source`` to ``target``."""

    def available(self) -> tuple[bool, str]:
        """Return ``(usable, detail)`` without loading heavy state."""
        return True, "ok"

    def warmup(self) -> None:
        """Optional eager load; must stay a no-op for cheap backends."""


class PassthroughBackend(TranslationBackend):
    """Echo backend used for wiring tests and no-model dry runs."""

    name = "passthrough"

    def translate(self, text: str, source: Language, target: Language) -> str:
        return text


class NllbBackend(TranslationBackend):
    """Direct translation with NLLB-200 (local, ``local_files_only``)."""

    name = "nllb"

    def __init__(
        self,
        model_name: Optional[str] = None,
        *,
        cache_dir=None,
        local_files_only: bool = True,
        device: Optional[str] = None,
    ) -> None:
        self.model_name = model_name or nllb_model_name()
        self.cache_dir = str(cache_dir) if cache_dir else str(model_root())
        self.local_files_only = local_files_only
        self.device = device
        self._explicit_device = device is not None
        self._fell_back = False
        self.notice = ""
        self._model = None
        self._tokenizer = None

    def available(self) -> tuple[bool, str]:
        try:
            import transformers  # noqa: F401
        except Exception:
            return False, "transformers is not installed; run host_app\\setup.ps1"
        if self.local_files_only and not self._weights_present():
            return (
                False,
                f"{self.model_name} is not downloaded; run host_app\\download_models.py",
            )
        return True, f"{self.model_name} (local cache: {self.cache_dir})"

    def warmup(self) -> None:
        self._load()

    def translate(self, text: str, source: Language, target: Language) -> str:
        if not text:
            return ""
        if source.is_auto or source.nllb_code is None:
            raise TranslationError("the source language must be resolved before translation")
        if target.nllb_code is None:
            raise TranslationError("the target language must be a concrete language")
        for _attempt in range(2):  # CUDA attempt, then at most one CPU attempt
            tokenizer, model = self._load()
            try:
                return self._generate(tokenizer, model, text, source, target)
            except Exception as exc:
                if self._maybe_fallback(exc):
                    continue
                raise
        raise TranslationError("NLLB inference failed")  # pragma: no cover

    def _generate(self, tokenizer, model, text: str, source: Language, target: Language) -> str:
        try:
            tokenizer.src_lang = source.nllb_code
            inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
            forced_bos = tokenizer.convert_tokens_to_ids(target.nllb_code)
            if forced_bos is None or forced_bos == tokenizer.unk_token_id:
                raise TranslationError(
                    f"NLLB does not know the target tag {target.nllb_code!r}"
                )
            outputs = model.generate(
                **inputs,
                forced_bos_token_id=forced_bos,
                max_new_tokens=256,
            )
        except TranslationError:
            raise
        except Exception as exc:  # pragma: no cover - depends on model state
            raise TranslationError(f"NLLB inference failed: {exc}") from exc
        return tokenizer.decode(outputs[0], skip_special_tokens=True).strip()

    def _maybe_fallback(self, exc: BaseException) -> bool:
        """Handle a CUDA runtime failure once, then stay on CPU."""
        if self._fell_back or self._explicit_device or self.device != "cuda":
            return False
        if not is_cuda_runtime_error(exc):
            return False
        self._fell_back = True
        self._model = None
        self._tokenizer = None
        self.device = "cpu"
        self.notice = f"CUDA unavailable ({exc}); translation switched to CPU"
        return True

    def _load(self):
        if self._model is not None:
            return self._tokenizer, self._model
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except Exception as exc:
            raise TranslationError(
                "transformers is not installed; run host_app\\setup.ps1"
            ) from exc
        try:
            tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir,
                local_files_only=self.local_files_only,
            )
            model = AutoModelForSeq2SeqLM.from_pretrained(
                self.model_name,
                cache_dir=self.cache_dir,
                local_files_only=self.local_files_only,
            )
        except Exception as exc:
            raise TranslationError(
                f"translation model {self.model_name!r} is not available locally "
                f"({exc}); run host_app\\download_models.py"
            ) from exc
        device = self.device
        if device is None:
            device = _auto_device()
        try:
            model.to(device)
        except Exception:  # pragma: no cover - CPU-only build
            device = "cpu"
            model.to("cpu")
        model.eval()
        self._model, self._tokenizer, self.device = model, tokenizer, device
        return tokenizer, model

    def _weights_present(self) -> bool:
        from pathlib import Path

        root = Path(self.cache_dir)
        if not root.exists():
            return False
        needle = self.model_name.replace("/", "--")
        return any(needle in path.name for path in root.rglob("*"))


def _auto_device() -> str:
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda"
    except Exception:
        pass
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


@dataclass(frozen=True)
class TranslationService:
    """Front-end used by the pipeline: resolves Auto and absorbs errors."""

    backend: TranslationBackend

    @property
    def name(self) -> str:
        return self.backend.name

    @property
    def notice(self) -> str:
        """Backend notice (e.g. a CUDA -> CPU fallback); empty otherwise."""
        return getattr(self.backend, "notice", "")

    def available(self) -> tuple[bool, str]:
        return self.backend.available()

    def translate(
        self,
        text: str,
        source: Language,
        target: Language,
        *,
        detected_language: Optional[str] = None,
    ) -> TranslationResult:
        resolved = source
        try:
            if source.is_auto:
                resolved = resolve_source_language(source, detected_language)
            if resolved.key == target.key:
                return TranslationResult(
                    text=text,
                    ok=True,
                    backend=self.backend.name,
                    source_key=resolved.key,
                    target_key=target.key,
                )
            translated = self.backend.translate(text, resolved, target)
        except Exception as exc:
            return TranslationResult(
                text=text,
                ok=False,
                error=str(exc),
                backend=self.backend.name,
                source_key=resolved.key,
                target_key=target.key,
            )
        return TranslationResult(
            text=translated,
            ok=True,
            backend=self.backend.name,
            source_key=resolved.key,
            target_key=target.key,
        )


def create_backend(name: Optional[str] = None) -> TranslationBackend:
    """Build the configured backend (``RESTS_TRANSLATION_BACKEND``)."""
    choice = (name or os.environ.get(ENV_TRANSLATION_BACKEND) or "nllb").strip().lower()
    if choice == "nllb":
        return NllbBackend()
    if choice == "passthrough":
        return PassthroughBackend()
    raise ValueError(
        f"unknown translation backend {choice!r}; expected one of {BACKEND_ENV_CHOICES}"
    )
