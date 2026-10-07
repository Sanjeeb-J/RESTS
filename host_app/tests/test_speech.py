"""Windows voice matching with fake voice providers."""

from __future__ import annotations

import pytest

from rests_host.languages import AUTO, TARGET_LANGUAGES, language_by_key
from rests_host.tts import InstalledVoice, SpeechError, WindowsSpeech


def fake_voices():
    return [
        InstalledVoice(name="Microsoft Hazel Desktop", lcid=0x0809),  # en-GB
        InstalledVoice(name="Microsoft Haruka Desktop", lcid=0x0411),  # ja-JP
    ]


def test_voice_matches_by_windows_primary_language():
    speech = WindowsSpeech(voice_provider=fake_voices)
    english = speech.voice_for(language_by_key("en"))
    assert english is not None and english.name == "Microsoft Hazel Desktop"
    japanese = speech.voice_for(language_by_key("ja"))
    assert japanese is not None and japanese.name == "Microsoft Haruka Desktop"


def test_missing_voice_reports_captions_only():
    speech = WindowsSpeech(voice_provider=fake_voices)
    for key in ("ml", "hi", "ko"):
        status = speech.status(language_by_key(key))
        assert status.available is False
        assert "no Windows voice" in status.detail
        assert "captions only" in status.detail
        assert "Microsoft Hazel Desktop" in status.detail  # shows what exists


def test_auto_language_has_no_speech_target():
    speech = WindowsSpeech(voice_provider=fake_voices)
    status = speech.status(AUTO)
    assert status.available is False
    assert "no target language" in status.detail


def test_speech_unavailable_when_pywin32_missing():
    def raise_provider():
        raise SpeechError("pywin32 is not installed; run host_app\\setup.ps1")

    speech = WindowsSpeech(voice_provider=raise_provider)
    status = speech.status(language_by_key("en"))
    assert status.available is False
    assert "pywin32" in status.detail
    assert speech.voices() == []


def test_speak_is_a_safe_no_op_when_unavailable():
    speech = WindowsSpeech(voice_provider=fake_voices)
    status = speech.speak("namaskaram", language_by_key("ko"))
    assert status.available is False


def test_speak_selects_the_matching_voice_lcid():
    calls = []

    def fake_speak(text, lcid, block):
        calls.append((text, lcid, block))

    speech = WindowsSpeech(voice_provider=fake_voices, speak_function=fake_speak)
    status = speech.speak("konnichiwa", language_by_key("ja"))
    assert status.available is True
    assert status.voice == "Microsoft Haruka Desktop"
    assert calls == [("konnichiwa", 0x0411, True)]

    english = speech.speak("hello", language_by_key("en"), block=False)
    assert english.available is True
    assert calls[-1] == ("hello", 0x0809, False)  # first matching English voice


def test_speak_reports_backend_failure_without_raising():
    def failing_speak(text, lcid, block):
        raise RuntimeError("audio device is muted by Windows")

    speech = WindowsSpeech(voice_provider=fake_voices, speak_function=failing_speak)
    status = speech.speak("hello", language_by_key("en"))
    assert status.available is False
    assert "audio device is muted" in status.detail


def test_speak_with_empty_text_does_nothing():
    calls = []
    speech = WindowsSpeech(voice_provider=fake_voices, speak_function=lambda *a: calls.append(a))
    status = speech.speak("", language_by_key("en"))
    assert status.available is True
    assert calls == []


def test_supported_targets_lists_only_installed_languages():
    speech = WindowsSpeech(voice_provider=fake_voices)
    assert speech.supported_targets(TARGET_LANGUAGES) == ["English", "Japanese"]


def test_installed_voice_helpers():
    voice = InstalledVoice(name="Microsoft Zira Desktop", lcid=0x0409)
    assert voice.primary_lang == 0x09
    assert voice.language_name == "English"
    assert "Zira" in voice.describe
    with pytest.raises(AttributeError):
        voice.primary_lang = 1  # frozen dataclass
