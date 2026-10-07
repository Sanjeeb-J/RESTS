"""Push-to-talk command ordering against the fake firmware."""

from __future__ import annotations

import pytest

from conftest import FakeRestsDevice, wait_until
from rests_host.languages import AUTO, language_by_key
from rests_host.ptt import PttController
from rests_host.uart import RenodeUart


@pytest.fixture
def device():
    fake = FakeRestsDevice()
    yield fake
    fake.close()


@pytest.fixture
def pair():
    return {"source": language_by_key("en"), "target": language_by_key("ml")}


@pytest.fixture
def uart(device):
    client = RenodeUart(device.host, device.port, handshake_timeout=3.0)
    assert client.connect(), "fake device must accept the handshake"
    yield client
    client.close()


def make_controller(uart, pair, events=None):
    return PttController(
        uart,
        lambda: (pair["source"], pair["target"]),
        on_event=events.append if events is not None else None,
    )


def test_press_sends_lang_before_ptt_begin(uart, device, pair):
    ptt = make_controller(uart, pair)
    assert ptt.press() is True
    assert device.wait_for("PTT:BEGIN")
    assert device.wait_for("LANG:en:ml")
    assert ptt.active is True

    lang_index = device.index_of("LANG:en:ml")
    begin_index = device.index_of("PTT:BEGIN")
    assert lang_index != -1 and begin_index != -1
    assert lang_index < begin_index
    assert device.commands[:begin_index] == ["HELLO?", "LANG:en:ml"]


def test_release_sends_ptt_end_after_begin(uart, device, pair):
    ptt = make_controller(uart, pair)
    ptt.press()
    ptt.release()
    assert device.wait_for("PTT:END")
    assert ptt.active is False

    begin_index = device.index_of("PTT:BEGIN")
    end_index = device.index_of("PTT:END")
    assert begin_index < end_index
    # exactly one BEGIN and one END for one session
    assert device.commands.count("PTT:BEGIN") == 1
    assert device.commands.count("PTT:END") == 1


def test_session_order_is_lang_begin_end(uart, device, pair):
    ptt = make_controller(uart, pair)
    ptt.press()
    ptt.release()
    assert device.wait_for("PTT:END")
    session = [c for c in device.commands if c.startswith("LANG:") or c.startswith("PTT:")]
    assert session == ["LANG:en:ml", "PTT:BEGIN", "PTT:END"]
    assert ptt.session == 1
    assert ptt.last_pair == (pair["source"], pair["target"])


def test_key_auto_repeat_does_not_duplicate_begin(uart, device, pair):
    ptt = make_controller(uart, pair)
    assert ptt.press() is True
    assert ptt.press() is False  # auto-repeat while held
    assert ptt.press() is False
    ptt.release()
    assert device.wait_for("PTT:END")
    assert ptt.release() is False  # release without press
    assert device.commands.count("PTT:BEGIN") == 1
    assert device.commands.count("PTT:END") == 1


def test_release_without_press_is_ignored(uart, device, pair):
    ptt = make_controller(uart, pair)
    assert ptt.release() is False
    assert not device.wait_for("PTT:END", timeout=0.3)
    assert device.commands.count("PTT:END") == 0


def test_language_change_is_sent_on_next_press(uart, device, pair):
    ptt = make_controller(uart, pair)
    ptt.press()
    ptt.release()
    assert device.wait_for("PTT:END")

    pair["source"] = AUTO
    pair["target"] = language_by_key("ko")
    ptt.press()
    ptt.release()
    assert wait_until(lambda: device.commands.count("LANG:auto:ko") == 1)
    assert device.commands.count("LANG:en:ml") == 1
    assert device.commands.count("PTT:BEGIN") == 2
    assert device.commands.count("PTT:END") == 2
    order = device.commands
    assert order.index("LANG:auto:ko") < order.index("PTT:BEGIN", order.index("LANG:auto:ko"))


def test_press_without_connection_fails_cleanly(device, pair):
    uart = RenodeUart(device.host, device.port)  # never connected
    ptt = make_controller(uart, pair)
    assert ptt.press() is False
    assert ptt.active is False
    assert ptt.release() is False
    assert device.commands == [] or device.commands == ["HELLO?"]


def test_press_emits_status_events(uart, device, pair):
    events = []
    ptt = make_controller(uart, pair, events)
    ptt.press()
    ptt.release()
    assert device.wait_for("PTT:END")
    joined = " | ".join(events)
    assert "PTT:BEGIN" in joined and "PTT:END" in joined
    assert "started" in joined and "ended" in joined
