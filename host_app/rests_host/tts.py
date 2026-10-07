"""Optional spoken output through installed Windows (SAPI) voices.

Speech is a bonus: when no Windows voice matches the target language the
captions keep working and the UI only reports that speech is unavailable
(malayalam voices in particular are rarely shipped with Windows).

Implementation notes
--------------------
* Enumeration and selection use the SAPI voice *category* through
  ``SAPI.SpObjectTokenCategory`` (``win32com`` dynamic dispatch).  The
  obvious ``SpVoice.Voices`` property is **not** exposed over
  ``IDispatch``, which is why the category object is used instead.
* Voice selection is exact (full LCID); availability is decided by the
  Windows primary language ID so that e.g. ``en-GB`` still satisfies an
  English target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from .languages import BASE_LANGUAGES, Language

#: SAPI category that holds the classic (desktop) voices.
_SAPI_VOICE_CATEGORY = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices"

#: LANGID low 10 bits -> Windows language name (display only).  Derived
#: from the language table so e.g. 0x4C is Malayalam and 0x49 is Tamil.
_PRIMARY_LANG_NAMES = {
    lang.windows_primary_lang: lang.label
    for lang in BASE_LANGUAGES
    if lang.windows_primary_lang is not None
}


class SpeechError(RuntimeError):
    """Raised when Windows TTS cannot be used at all."""


@dataclass(frozen=True)
class InstalledVoice:
    name: str
    lcid: int

    @property
    def primary_lang(self) -> int:
        return self.lcid & 0x3FF

    @property
    def language_name(self) -> str:
        return _PRIMARY_LANG_NAMES.get(self.primary_lang, f"LCID 0x{self.lcid:X}")

    @property
    def describe(self) -> str:
        return f"{self.name} [{self.language_name}]"


@dataclass(frozen=True)
class SpeechStatus:
    available: bool
    detail: str
    voice: Optional[str] = None


VoiceProvider = Callable[[], List[InstalledVoice]]
SpeakFunction = Callable[[str, int, bool], None]


def _sapi_voices() -> List[InstalledVoice]:
    try:
        import pythoncom
        import win32com.client.dynamic as dynamic
    except Exception as exc:
        raise SpeechError(
            "pywin32 is not installed; run host_app\\setup.ps1 for spoken output"
        ) from exc
    try:
        pythoncom.CoInitialize()
        category = dynamic.Dispatch("SAPI.SpObjectTokenCategory")
        category.SetId(_SAPI_VOICE_CATEGORY, False)
        tokens = category.EnumerateTokens()
        result: List[InstalledVoice] = []
        for index in range(tokens.Count):
            token = tokens.Item(index)
            raw = str(token.GetAttribute("Language") or "0").split(";")[0]
            try:
                lcid = int(raw, 16)
            except ValueError:
                lcid = 0
            result.append(InstalledVoice(name=str(token.GetDescription()), lcid=lcid))
        return result
    except SpeechError:
        raise
    except Exception as exc:
        raise SpeechError(f"could not enumerate Windows voices: {exc}") from exc


def _sapi_speak(text: str, lcid: int, block: bool) -> None:
    """Select the voice with ``lcid`` and speak (SAPI)."""
    import pythoncom
    import win32com.client.dynamic as dynamic

    pythoncom.CoInitialize()
    speaker = dynamic.Dispatch("SAPI.SpVoice")
    category = dynamic.Dispatch("SAPI.SpObjectTokenCategory")
    category.SetId(_SAPI_VOICE_CATEGORY, False)
    tokens = category.EnumerateTokens()
    for index in range(tokens.Count):
        token = tokens.Item(index)
        raw = str(token.GetAttribute("Language") or "0").split(";")[0]
        try:
            token_lcid = int(raw, 16)
        except ValueError:
            continue
        if token_lcid == lcid:
            speaker.Voice = token
            break
    speaker.Speak(text, 0 if block else 1)  # 1 = SVSFlagsAsync


class WindowsSpeech:
    """Match target languages to installed SAPI voices."""

    def __init__(
        self,
        voice_provider: Optional[VoiceProvider] = None,
        speak_function: Optional[SpeakFunction] = None,
    ) -> None:
        self._voice_provider = voice_provider or _sapi_voices
        self._speak_function = speak_function or _sapi_speak
        self._voices: Optional[List[InstalledVoice]] = None
        self._error: str = ""

    def voices(self) -> List[InstalledVoice]:
        if self._voices is None:
            try:
                self._voices = self._voice_provider()
                self._error = ""
            except SpeechError as exc:
                self._voices = []
                self._error = str(exc)
        return list(self._voices)

    def voice_for(self, language: Language) -> Optional[InstalledVoice]:
        wanted = language.windows_primary_lang
        if wanted is None:
            return None
        for voice in self.voices():
            if voice.primary_lang == wanted:
                return voice
        return None

    def status(self, language: Language) -> SpeechStatus:
        if language.windows_primary_lang is None:
            if language.is_auto:
                return SpeechStatus(False, "no target language selected")
            return SpeechStatus(
                False,
                f"no Windows locale for {language.label}; captions only",
            )
        voice = self.voice_for(language)
        if voice is not None:
            return SpeechStatus(True, f"voice ready: {voice.describe}", voice.name)
        if self._error:
            return SpeechStatus(False, self._error)
        installed = ", ".join(v.describe for v in self.voices()) or "none"
        return SpeechStatus(
            False,
            f"no Windows voice for {language.label}; captions only (installed: {installed})",
        )

    def speak(self, text: str, language: Language, *, block: bool = True) -> SpeechStatus:
        status = self.status(language)
        if not status.available or not text:
            return status
        voice = self.voice_for(language)
        if voice is None:  # pragma: no cover - status already said available
            return SpeechStatus(False, "voice disappeared", status.voice)
        try:
            self._speak_function(text, voice.lcid, block)
        except Exception as exc:
            return SpeechStatus(False, f"speech failed: {exc}", status.voice)
        return status

    def supported_targets(self, languages: Sequence[Language]) -> List[str]:
        return [lang.label for lang in languages if self.voice_for(lang) is not None]


def primary_language_ids() -> Sequence[int]:
    from .languages import _WINDOWS_PRIMARY_LANG

    return tuple(sorted(value for value in _WINDOWS_PRIMARY_LANG.values() if value))
