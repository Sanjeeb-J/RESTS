"""UART protocol handling against a fake Renode socket terminal."""

from __future__ import annotations

import pytest

from conftest import FakeRestsDevice, READY_MESSAGE, wait_until
from rests_host.uart import (
    ConnectionState,
    RenodeUart,
    UartError,
)


@pytest.fixture
def device():
    fake = FakeRestsDevice()
    yield fake
    fake.close()


@pytest.fixture
def events():
    states, replies = [], []
    return states, replies


def make_uart(device, states, replies, **kwargs):
    return RenodeUart(
        device.host,
        device.port,
        on_state=states.append,
        on_reply=replies.append,
        **kwargs,
    )


def test_handshake_sends_hello_and_reaches_ready(device):
    states, replies = [], []
    uart = make_uart(device, states, replies)
    try:
        assert uart.connect() is True
        assert uart.state is ConnectionState.READY
        assert uart.is_ready
        assert device.commands[0] == "HELLO?"
        assert any(event.line == READY_MESSAGE for event in replies)
        seen = [event.state for event in states]
        assert ConnectionState.CONNECTING in seen
        assert ConnectionState.HANDSHAKING in seen
        assert seen[-1] is ConnectionState.READY
    finally:
        uart.close()


def test_all_commands_are_crlf_terminated(device):
    states, replies = ([], [])
    uart = make_uart(device, states, replies)
    try:
        assert uart.connect()
        uart.send_command("PING")
        uart.send_command("LANG:en:ml")
        assert wait_until(lambda: len(device.commands) >= 3)
        for frame in device.frames:
            assert frame.endswith(b"\r\n"), frame
            assert frame.count(b"\n") == 1
            assert frame[:-2].find(b"\n") == -1
        assert device.commands[1:] == ["PING", "LANG:en:ml"]
    finally:
        uart.close()


def test_replies_are_dispatched_to_the_callback(device):
    states, replies = ([], [])
    uart = make_uart(device, states, replies)
    try:
        assert uart.connect()
        uart.send_command("PING")
        assert wait_until(lambda: any(r.line == "PONG" for r in replies))
    finally:
        uart.close()


def test_error_replies_are_flagged(device):
    states, replies = ([], [])
    uart = make_uart(device, states, replies)
    try:
        assert uart.connect()
        uart.send_command("NOT_A_COMMAND")
        assert wait_until(lambda: any(r.line == "ERR:UNKNOWN_CMD" for r in replies))
        error = [r for r in replies if r.line == "ERR:UNKNOWN_CMD"][0]
        assert error.is_error is True
        assert [r for r in replies if r.line == "PONG"] == []
    finally:
        uart.close()


def test_connect_failure_is_reported_not_raised():
    import socket

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    free_port = probe.getsockname()[1]
    probe.close()

    states, replies = ([], [])
    uart = RenodeUart("127.0.0.1", free_port, on_state=states.append, handshake_timeout=1.0)
    assert uart.connect() is False
    assert uart.state is ConnectionState.ERROR
    assert "connect failed" in uart.state_detail
    uart.close()
    assert uart.state is ConnectionState.ERROR  # error state is preserved


def test_remote_close_ends_the_session(device):
    device.close_after_first_command = True
    states, replies = ([], [])
    uart = make_uart(device, states, replies, handshake_timeout=2.0)
    try:
        assert uart.connect() is True  # HELLO? is answered before closing
        assert wait_until(
            lambda: uart.state is ConnectionState.DISCONNECTED, timeout=3.0
        ), uart.state
    finally:
        uart.close()


def test_send_command_validation():
    uart = RenodeUart("127.0.0.1", 1)
    with pytest.raises(UartError):
        uart.send_command("")
    with pytest.raises(UartError):
        uart.send_command("   ")
    with pytest.raises(UartError):
        uart.send_command("PING\nLANG:en:ml")
    assert uart.send_command("PING") is False  # not connected
    uart.close()


def test_commands_are_queued_fifo(device):
    states, replies = ([], [])
    uart = make_uart(device, states, replies)
    try:
        assert uart.connect()
        for index in range(5):
            assert uart.send_command("PTT:BEGIN") is True
        assert uart.send_command("PING")
        assert wait_until(lambda: "PING" in device.commands)
        sequence = [c for c in device.commands if c.startswith("PTT:BEGIN")]
        assert sequence == ["PTT:BEGIN"] * 5
        assert device.commands.index("PING") > device.commands.index("PTT:BEGIN")
    finally:
        uart.close()
