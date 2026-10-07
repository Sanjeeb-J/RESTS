"""Language mapping: UI labels <-> Whisper <-> NLLB <-> UART tokens."""

from __future__ import annotations

import pytest

from rests_host.languages import (
    AUTO,
    BASE_LANGUAGES,
    OMITTED_LANGUAGES,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
    lang_command,
    language_by_key,
    language_by_label,
    language_pair_is_valid,
    nllb_language_for_key,
    resolve_source_language,
    source_labels,
    target_labels,
    whisper_language_for_key,
)


def firmware_accepts_lang_command(command: str) -> bool:
    """Mirror of the parser in Core/Src/main.c RESTS_HandleCommand()."""
    if not command.startswith("LANG:"):
        return False
    rest = command[5:]
    separator = rest.find(":")
    if separator <= 0:  # source must be non-empty
        return False
    source, target = rest[:separator], rest[separator + 1 :]
    if not target or ":" in target:
        return False
    return True


def test_source_choices_match_requirements():
    labels = list(source_labels())
    # Auto plus the 95 languages of the Whisper/NLLB-200 intersection.
    assert labels[:6] == [
        "Auto",
        "English",
        "Malayalam",
        "Hindi",
        "Japanese",
        "Korean",
    ]
    assert labels[0] == "Auto"
    assert SOURCE_LANGUAGES[0] is AUTO
    assert len(labels) == 96
    assert len(set(labels)) == len(labels)  # no duplicate labels


def test_target_choices_are_the_supported_languages():
    labels = list(target_labels())
    assert labels[:5] == ["English", "Malayalam", "Hindi", "Japanese", "Korean"]
    assert AUTO not in TARGET_LANGUAGES
    assert len(TARGET_LANGUAGES) == 95
    assert len(set(labels)) == len(labels)


def test_menu_order_is_original_five_then_alphabetical():
    labels = [language.label for language in TARGET_LANGUAGES]
    assert labels[5:] == sorted(labels[5:], key=str.lower)


def test_omitted_languages_are_documented():
    omitted = dict(OMITTED_LANGUAGES)
    assert omitted == {
        "br": "Breton",
        "haw": "Hawaiian",
        "la": "Latin",
        "mg": "Malagasy",
        "sq": "Albanian",
    }
    known = {language.key for language in BASE_LANGUAGES}
    assert known.isdisjoint(omitted)


@pytest.mark.parametrize(
    "key,whisper,nllb,uart",
    [
        ("en", "en", "eng_Latn", "en"),
        ("ml", "ml", "mal_Mlym", "ml"),
        ("hi", "hi", "hin_Deva", "hi"),
        ("ja", "ja", "jpn_Jpan", "ja"),
        ("ko", "ko", "kor_Hang", "ko"),
        ("ta", "ta", "tam_Taml", "ta"),
        ("yue", "yue", "yue_Hant", "yue"),
        ("ms", "ms", "zsm_Latn", "ms"),
        ("ne", "ne", "npi_Deva", "ne"),
        ("no", "no", "nob_Latn", "no"),
        ("ps", "ps", "pbt_Arab", "ps"),
        ("zh", "zh", "zho_Hans", "zh"),
    ],
)
def test_code_spaces(key, whisper, nllb, uart):
    assert whisper_language_for_key(key) == whisper
    assert nllb_language_for_key(key) == nllb
    assert language_by_key(key).uart_token == uart


def test_every_language_uses_whisper_codes_as_keys():
    from faster_whisper.transcribe import _LANGUAGE_CODES

    offered = {language.key for language in BASE_LANGUAGES}
    omitted = {key for key, _label in OMITTED_LANGUAGES}
    assert offered == set(_LANGUAGE_CODES) - omitted


def test_nllb_tags_have_the_documented_shape():
    for language in BASE_LANGUAGES:
        assert language.nllb_code
        prefix, script = language.nllb_code.split("_", 1)
        assert prefix.islower() and prefix.isalpha()
        assert script[:1].isupper() and script[1:].islower()


def test_windows_primary_language_ids_are_plausible():
    for language in BASE_LANGUAGES:
        primary = language.windows_primary_lang
        if primary is None:
            assert language.is_auto is False
            continue
        assert 0 < primary <= 0x3FF
    # LANGID 0x4C is Malayalam (0x49 is Tamil - keep them apart).
    assert language_by_key("ml").windows_primary_lang == 0x4C
    assert language_by_key("ta").windows_primary_lang == 0x49
    # Eight targets have no Windows locale: they stay captions-only.
    assert sorted(
        language.key
        for language in BASE_LANGUAGES
        if language.windows_primary_lang is None
    ) == ["ht", "jw", "ln", "sn", "su", "tl", "yi", "yue"]


def test_auto_language_has_no_model_codes():
    assert AUTO.is_auto
    assert AUTO.whisper_code is None
    assert AUTO.nllb_code is None
    assert AUTO.uart_token == "auto"


def test_label_lookup_round_trips():
    for language in SOURCE_LANGUAGES:
        assert language_by_label(language.label) is language
    for language in BASE_LANGUAGES:
        assert language_by_key(language.key) is language
    with pytest.raises(ValueError):
        language_by_label("Klingon")


def test_lang_command_format_is_firmware_compatible():
    for source in SOURCE_LANGUAGES:
        for target in TARGET_LANGUAGES:
            command = lang_command(source, target)
            assert command == f"LANG:{source.uart_token}:{target.uart_token}"
            assert command.endswith("\r") is False
            assert "\r" not in command and "\n" not in command
            assert firmware_accepts_lang_command(command)
            assert language_pair_is_valid(source, target)


def test_lang_command_defaults_to_the_documented_pair():
    assert lang_command(language_by_key("en"), language_by_key("ml")) == "LANG:en:ml"
    assert lang_command(AUTO, language_by_key("hi")) == "LANG:auto:hi"


def test_lang_command_rejects_broken_tokens():
    broken = language_by_key("en")
    from dataclasses import replace

    bad_source = replace(broken, uart_token="")
    bad_target = replace(broken, uart_token="a:b")
    with pytest.raises(ValueError):
        lang_command(bad_source, TARGET_LANGUAGES[0])
    with pytest.raises(ValueError):
        lang_command(SOURCE_LANGUAGES[0], bad_target)
    assert not language_pair_is_valid(bad_source, TARGET_LANGUAGES[0])


def test_resolve_source_language_uses_detection():
    auto = AUTO
    assert resolve_source_language(auto, "ja") is language_by_key("ja")
    concrete = language_by_key("ko")
    assert resolve_source_language(concrete, "en") is concrete  # detection ignored


def test_resolve_source_language_rejects_unsupported_detection():
    with pytest.raises(ValueError, match="not one of the supported languages"):
        resolve_source_language(AUTO, "br")  # Breton: no NLLB-200 tag
    with pytest.raises(ValueError, match="Breton"):
        resolve_source_language(AUTO, "br")
    with pytest.raises(ValueError, match="language detection failed"):
        resolve_source_language(AUTO, None)
