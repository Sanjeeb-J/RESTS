"""Language tables shared by the UI, the UART protocol and the ML backends.

Single source of truth for four different code spaces:

* UI labels shown in the combo boxes ("English", "Tamil", ...)
* faster-whisper language codes ("en", "ta", ...)
* NLLB-200 language tags ("eng_Latn", "tam_Taml", ...)
* UART tokens used by the firmware command ``LANG:<source>:<target>``

**Supported set** - a language is offered only when *both* configured
models know it, i.e. it is in faster-whisper's 100-code transcription
alphabet *and* has a tag in ``facebook/nllb-200-distilled-600M``'s local
tokenizer vocabulary. That intersection is 95 languages plus *Auto* for
the source list; see :data:`OMITTED_LANGUAGES` for the five Whisper
languages that NLLB-200 does not cover.

This is deliberately **not** "every language Google Translate offers":
the menu only contains what the two offline checkpoints can actually
transcribe and translate, and anything else is rejected with a clear
message instead of being mistranslated.

The firmware accepts any non-empty token that contains no colon, so the
tokens here stay short and stable: the Whisper code for concrete
languages (``en``, ``ta``, ``yue`` ...) and ``auto`` while the source
language is still being detected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

#: UART token sent when the source language is "Auto" (detected later by
#: the speech recogniser, therefore unknown while PTT is being pressed).
AUTO_UART_TOKEN = "auto"

#: Whisper codes of the transcription model that this build does **not**
#: offer, because the local NLLB-200 vocabulary has no tag for them.
#: Kept public so the self-check and the tests can document the gap.
OMITTED_LANGUAGES: Tuple[Tuple[str, str], ...] = (
    ("br", "Breton"),
    ("haw", "Hawaiian"),
    ("la", "Latin"),
    ("mg", "Malagasy"),
    ("sq", "Albanian"),
)

#: ``(key, label, nllb_tag, windows_primary_lang)`` - the Whisper code and
#: the UART token are both ``key``. Windows primary language IDs (LANGID
#: & 0x3FF) are resolved from the real locale names with
#: ``LocaleNameToLCID``; ``None`` means Windows has no such locale, so
#: spoken output falls back to captions for that target.
_TABLE: Tuple[Tuple[str, str, str, Optional[int]], ...] = (
    # -- the original five, kept first in the menu -------------------------
    ("en", "English", "eng_Latn", 0x09),
    ("ml", "Malayalam", "mal_Mlym", 0x4C),
    ("hi", "Hindi", "hin_Deva", 0x39),
    ("ja", "Japanese", "jpn_Jpan", 0x11),
    ("ko", "Korean", "kor_Hang", 0x12),
    # -- the rest, alphabetical by label -----------------------------------
    ("af", "Afrikaans", "afr_Latn", 0x36),
    ("am", "Amharic", "amh_Ethi", 0x5E),
    ("ar", "Arabic", "arb_Arab", 0x01),
    ("hy", "Armenian", "hye_Armn", 0x2B),
    ("as", "Assamese", "asm_Beng", 0x4D),
    ("az", "Azerbaijani", "azj_Latn", 0x2C),
    ("ba", "Bashkir", "bak_Cyrl", 0x6D),
    ("eu", "Basque", "eus_Latn", 0x2D),
    ("be", "Belarusian", "bel_Cyrl", 0x23),
    ("bn", "Bengali", "ben_Beng", 0x45),
    ("bs", "Bosnian", "bos_Latn", 0x1A),
    ("bg", "Bulgarian", "bul_Cyrl", 0x02),
    ("my", "Burmese", "mya_Mymr", 0x55),
    ("yue", "Cantonese", "yue_Hant", None),
    ("ca", "Catalan", "cat_Latn", 0x03),
    ("zh", "Chinese (Simplified)", "zho_Hans", 0x04),
    ("hr", "Croatian", "hrv_Latn", 0x1A),
    ("cs", "Czech", "ces_Latn", 0x05),
    ("da", "Danish", "dan_Latn", 0x06),
    ("nl", "Dutch", "nld_Latn", 0x13),
    ("et", "Estonian", "est_Latn", 0x25),
    ("fo", "Faroese", "fao_Latn", 0x38),
    ("fi", "Finnish", "fin_Latn", 0x0B),
    ("fr", "French", "fra_Latn", 0x0C),
    ("gl", "Galician", "glg_Latn", 0x56),
    ("ka", "Georgian", "kat_Geor", 0x37),
    ("de", "German", "deu_Latn", 0x07),
    ("el", "Greek", "ell_Grek", 0x08),
    ("gu", "Gujarati", "guj_Gujr", 0x47),
    ("ht", "Haitian Creole", "hat_Latn", None),
    ("ha", "Hausa", "hau_Latn", 0x68),
    ("he", "Hebrew", "heb_Hebr", 0x0D),
    ("hu", "Hungarian", "hun_Latn", 0x0E),
    ("is", "Icelandic", "isl_Latn", 0x0F),
    ("id", "Indonesian", "ind_Latn", 0x21),
    ("it", "Italian", "ita_Latn", 0x10),
    ("jw", "Javanese", "jav_Latn", None),
    ("kn", "Kannada", "kan_Knda", 0x4B),
    ("kk", "Kazakh", "kaz_Cyrl", 0x3F),
    ("km", "Khmer", "khm_Khmr", 0x53),
    ("lo", "Lao", "lao_Laoo", 0x54),
    ("lv", "Latvian", "lvs_Latn", 0x26),
    ("ln", "Lingala", "lin_Latn", None),
    ("lt", "Lithuanian", "lit_Latn", 0x27),
    ("lb", "Luxembourgish", "ltz_Latn", 0x6E),
    ("mk", "Macedonian", "mkd_Cyrl", 0x2F),
    ("ms", "Malay", "zsm_Latn", 0x3E),
    ("mt", "Maltese", "mlt_Latn", 0x3A),
    ("mi", "Maori", "mri_Latn", 0x81),
    ("mr", "Marathi", "mar_Deva", 0x4E),
    ("mn", "Mongolian", "khk_Cyrl", 0x50),
    ("ne", "Nepali", "npi_Deva", 0x61),
    ("no", "Norwegian", "nob_Latn", 0x14),
    ("nn", "Norwegian Nynorsk", "nno_Latn", 0x14),
    ("oc", "Occitan", "oci_Latn", 0x82),
    ("ps", "Pashto", "pbt_Arab", 0x63),
    ("fa", "Persian", "pes_Arab", 0x29),
    ("pl", "Polish", "pol_Latn", 0x15),
    ("pt", "Portuguese", "por_Latn", 0x16),
    ("pa", "Punjabi", "pan_Guru", 0x46),
    ("ro", "Romanian", "ron_Latn", 0x18),
    ("ru", "Russian", "rus_Cyrl", 0x19),
    ("sa", "Sanskrit", "san_Deva", 0x4F),
    ("sr", "Serbian", "srp_Cyrl", 0x1A),
    ("sn", "Shona", "sna_Latn", None),
    ("sd", "Sindhi", "snd_Arab", 0x59),
    ("si", "Sinhala", "sin_Sinh", 0x5B),
    ("sk", "Slovak", "slk_Latn", 0x1B),
    ("sl", "Slovenian", "slv_Latn", 0x24),
    ("so", "Somali", "som_Latn", 0x77),
    ("es", "Spanish", "spa_Latn", 0x0A),
    ("su", "Sundanese", "sun_Latn", None),
    ("sw", "Swahili", "swh_Latn", 0x41),
    ("sv", "Swedish", "swe_Latn", 0x1D),
    ("tl", "Tagalog", "tgl_Latn", None),
    ("tg", "Tajik", "tgk_Cyrl", 0x28),
    ("ta", "Tamil", "tam_Taml", 0x49),
    ("tt", "Tatar", "tat_Cyrl", 0x44),
    ("te", "Telugu", "tel_Telu", 0x4A),
    ("th", "Thai", "tha_Thai", 0x1E),
    ("bo", "Tibetan", "bod_Tibt", 0x51),
    ("tr", "Turkish", "tur_Latn", 0x1F),
    ("tk", "Turkmen", "tuk_Latn", 0x42),
    ("uk", "Ukrainian", "ukr_Cyrl", 0x22),
    ("ur", "Urdu", "urd_Arab", 0x20),
    ("uz", "Uzbek", "uzn_Latn", 0x43),
    ("vi", "Vietnamese", "vie_Latn", 0x2A),
    ("cy", "Welsh", "cym_Latn", 0x52),
    ("yi", "Yiddish", "ydd_Hebr", None),
    ("yo", "Yoruba", "yor_Latn", 0x6A),
)

#: Whisper key -> Windows primary language ID (LANGID & 0x3FF), derived
#: from the table above; keys without a Windows locale are absent.
_WINDOWS_PRIMARY_LANG = {
    key: primary for key, _label, _nllb, primary in _TABLE if primary is not None
}


@dataclass(frozen=True)
class Language:
    """A language that the host application knows about."""

    key: str
    label: str
    whisper_code: Optional[str]
    nllb_code: Optional[str]
    uart_token: str
    windows_primary_lang: Optional[int]

    @property
    def is_auto(self) -> bool:
        return self.whisper_code is None


def _build(key: str, label: str, nllb: str, windows_primary: Optional[int]) -> Language:
    return Language(
        key=key,
        label=label,
        whisper_code=key,
        nllb_code=nllb,
        uart_token=key,
        windows_primary_lang=windows_primary,
    )


AUTO = Language(
    key="auto",
    label="Auto",
    whisper_code=None,
    nllb_code=None,
    uart_token=AUTO_UART_TOKEN,
    windows_primary_lang=None,
)

#: Concrete (non-Auto) languages, in menu order.
BASE_LANGUAGES: Tuple[Language, ...] = tuple(_build(*row) for row in _TABLE)

#: Source language choices: Auto + every concrete language.
SOURCE_LANGUAGES: Tuple[Language, ...] = (AUTO,) + BASE_LANGUAGES

#: Target language choices: the concrete languages (never Auto).
TARGET_LANGUAGES: Tuple[Language, ...] = BASE_LANGUAGES

_BY_KEY = {lang.key: lang for lang in SOURCE_LANGUAGES}
_BY_LABEL = {lang.label: lang for lang in SOURCE_LANGUAGES}


def source_labels() -> Sequence[str]:
    return [lang.label for lang in SOURCE_LANGUAGES]


def target_labels() -> Sequence[str]:
    return [lang.label for lang in TARGET_LANGUAGES]


def language_by_key(key: str) -> Language:
    try:
        return _BY_KEY[key]
    except KeyError:  # pragma: no cover - defensive
        raise ValueError(f"unknown language key: {key!r}") from None


def language_by_label(label: str) -> Language:
    try:
        return _BY_LABEL[label]
    except KeyError:
        raise ValueError(f"unknown language label: {label!r}") from None


def whisper_language_for_key(key: str) -> Optional[str]:
    """Return the faster-whisper language code, ``None`` for Auto."""
    return language_by_key(key).whisper_code


def nllb_language_for_key(key: str) -> Optional[str]:
    """Return the NLLB language tag, ``None`` for Auto."""
    return language_by_key(key).nllb_code


def unsupported_detection_message(detected_whisper_code: str) -> str:
    """Human readable explanation for a detected language we cannot translate."""
    omitted = {key: label for key, label in OMITTED_LANGUAGES}
    hint = ""
    if detected_whisper_code in omitted:
        hint = (
            f" - Whisper knows {omitted[detected_whisper_code]}, "
            f"but the NLLB-200 vocabulary has no tag for it"
        )
    examples = ", ".join(lang.label for lang in BASE_LANGUAGES[:6])
    return (
        f"detected language {detected_whisper_code!r} is not one of the "
        f"supported languages{hint}; {len(BASE_LANGUAGES)} languages are "
        f"available, e.g. {examples}, ..."
    )


def resolve_source_language(
    source: Language, detected_whisper_code: Optional[str]
) -> Language:
    """Resolve an ``Auto`` source using the language detected by Whisper.

    Raises ``ValueError`` when detection produced a language this build
    does not support, so the caller can keep the source caption and mark
    only the translation as unavailable instead of guessing wrongly.
    """
    if not source.is_auto:
        return source
    if detected_whisper_code:
        try:
            return language_by_key(detected_whisper_code)
        except ValueError:
            raise ValueError(
                unsupported_detection_message(detected_whisper_code)
            ) from None
    raise ValueError("language detection failed; the source language is unknown")


def lang_command(source: Language, target: Language) -> str:
    """Build the firmware command ``LANG:<source>:<target>``.

    Raises ``ValueError`` if a token would break the firmware's parsing
    rule (both tokens must be non-empty and must not contain a colon).
    """
    for name, token in (("source", source.uart_token), ("target", target.uart_token)):
        if not token or ":" in token or " " in token:
            raise ValueError(f"invalid {name} UART token: {token!r}")
    return f"LANG:{source.uart_token}:{target.uart_token}"


def language_pair_is_valid(source: Language, target: Language) -> bool:
    try:
        lang_command(source, target)
    except ValueError:
        return False
    return True
