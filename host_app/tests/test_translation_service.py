"""Translation service interfaces with fake backends (no model needed)."""

from __future__ import annotations

import importlib.util

import pytest

from rests_host.languages import AUTO, BASE_LANGUAGES, language_by_key
from rests_host.models import ENV_TRANSLATION_BACKEND
from rests_host.translate import (
    NllbBackend,
    PassthroughBackend,
    TranslationBackend,
    TranslationError,
    TranslationResult,
    TranslationService,
    create_backend,
)


class RecordingBackend(TranslationBackend):
    name = "recording"

    def __init__(self, reply="translated", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc
        self.calls = []

    def translate(self, text, source, target):
        self.calls.append((text, source, target))
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.reply


def test_backend_contract_is_abstract():
    with pytest.raises(TypeError):
        TranslationBackend()


def test_service_calls_backend_with_concrete_languages():
    backend = RecordingBackend(reply="namaskaram")
    service = TranslationService(backend)
    result = service.translate(
        "hello", AUTO, language_by_key("ml"), detected_language="en"
    )
    assert result.ok
    assert result.text == "namaskaram"
    assert result.backend == "recording"
    text, source, target = backend.calls[0]
    assert text == "hello"
    assert source.key == "en"          # Auto resolved via detection
    assert target.key == "ml"


def test_service_rejects_unsupported_detection_without_calling_backend():
    backend = RecordingBackend()
    service = TranslationService(backend)
    result = service.translate(
        "vanakkam", AUTO, language_by_key("en"), detected_language="br"
    )  # Breton: Whisper knows it, the NLLB vocabulary does not
    assert result.ok is False
    assert "not one of the supported languages" in result.error
    assert backend.calls == []
    assert result.text == "vanakkam"  # source caption preserved


def test_service_requires_detection_for_auto():
    backend = RecordingBackend()
    service = TranslationService(backend)
    result = service.translate("hello", AUTO, language_by_key("ja"), detected_language=None)
    assert result.ok is False
    assert "language detection failed" in result.error
    assert result.text == "hello"
    assert backend.calls == []


def test_backend_failure_keeps_source_text():
    backend = RecordingBackend(raise_exc=TranslationError("NLLB inference failed"))
    service = TranslationService(backend)
    result = service.translate("hello", language_by_key("en"), language_by_key("ko"))
    assert result.ok is False
    assert "NLLB inference failed" in result.error
    assert result.text == "hello"      # captions keep working
    assert result.source_key == "en" and result.target_key == "ko"


def test_same_language_short_circuits_the_backend():
    backend = RecordingBackend()
    service = TranslationService(backend)
    result = service.translate(
        "same", language_by_key("en"), language_by_key("en"), detected_language="en"
    )
    assert result.ok
    assert result.text == "same"
    assert backend.calls == []


def test_passthrough_backend_echoes_text():
    backend = PassthroughBackend()
    assert backend.name == "passthrough"
    assert backend.translate("x", language_by_key("en"), language_by_key("hi")) == "x"
    assert backend.available() == (True, "ok")


def test_backend_selection(monkeypatch):
    monkeypatch.delenv(ENV_TRANSLATION_BACKEND, raising=False)
    assert isinstance(create_backend(), NllbBackend)
    assert isinstance(create_backend("passthrough"), PassthroughBackend)
    monkeypatch.setenv(ENV_TRANSLATION_BACKEND, "passthrough")
    assert isinstance(create_backend(), PassthroughBackend)
    with pytest.raises(ValueError, match="unknown translation backend"):
        create_backend("cloud-service")


def test_nllb_backend_reports_availability_without_loading(monkeypatch):
    monkeypatch.delenv(ENV_TRANSLATION_BACKEND, raising=False)
    backend = NllbBackend()
    usable, detail = backend.available()
    assert isinstance(usable, bool)
    assert detail
    if importlib.util.find_spec("transformers") is None:
        assert usable is False
        assert "setup.ps1" in detail
    elif not backend._weights_present():
        assert usable is False
        assert "download_models.py" in detail


def test_nllb_backend_refuses_unresolved_source():
    backend = NllbBackend()
    with pytest.raises(TranslationError, match="source language must be resolved"):
        backend.translate("hello", AUTO, language_by_key("en"))
    with pytest.raises(TranslationError, match="concrete language"):
        backend.translate("hello", language_by_key("en"), AUTO)


def test_every_target_language_has_an_nllb_tag():
    for language in BASE_LANGUAGES:
        assert language.nllb_code and "_" in language.nllb_code
        assert NllbBackend  # backend is importable without transformers installed


def test_result_dataclass_defaults():
    result = TranslationResult(text="x", ok=True)
    assert result.error == ""
    assert result.backend == ""
