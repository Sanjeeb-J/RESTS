"""Optional real Renode + STM32 audio relay integration test.

Run explicitly with ``RESTS_RUN_RENODE_TESTS=1 pytest tests/test_renode_audio_relay.py``.
"""

from __future__ import annotations

import math
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from rests_host.uart import RenodeUart

pytestmark = pytest.mark.skipif(
    os.environ.get("RESTS_RUN_RENODE_TESTS") != "1",
    reason="set RESTS_RUN_RENODE_TESTS=1 to start Renode and test the ELF",
)

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_audio_roundtrips_through_real_renode_firmware():
    elf = ROOT / "Debug" / "RESTS_STM32.elf"
    launcher = ROOT / "host_app" / "renode" / "launch_renode.ps1"
    assert elf.is_file(), f"Build the Debug firmware first: {elf}"
    port = _free_port()
    process = subprocess.Popen(
        [
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(launcher), "-Headless", "-Port", str(port),
        ],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    replies = []
    pinged = threading.Event()
    uart = RenodeUart(
        "127.0.0.1", port, on_reply=lambda event: (replies.append(event), pinged.set() if event.line == "PONG" else None),
        handshake_timeout=2.0,
    )
    try:
        deadline = time.monotonic() + 30.0
        connected = False
        while time.monotonic() < deadline and process.poll() is None:
            if uart.connect():
                connected = True
                break
            time.sleep(0.25)
        assert connected, f"Renode failed to handshake (process exit={process.poll()})"

        samples = [0.18 * math.sin(2 * math.pi * 330 * i / 16_000) for i in range(16_000)]
        returned = uart.relay_audio(samples, timeout=4.0)
        assert len(returned) == len(samples)
        assert uart.send_command("PING")
        assert pinged.wait(3.0), f"PING did not receive PONG; replies={replies!r}"
    finally:
        uart.close()
        # The PowerShell launcher owns a Renode child, so stop that process
        # tree using the exact PID returned for this test's launcher.
        if process.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                process.wait(timeout=8.0)
            except subprocess.TimeoutExpired:
                process.kill()
