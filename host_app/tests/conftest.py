"""Shared test helpers: fake devices and path setup."""

from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

HOST_APP_DIR = Path(__file__).resolve().parents[1]
if str(HOST_APP_DIR) not in sys.path:
    sys.path.insert(0, str(HOST_APP_DIR))

READY_MESSAGE = "RESTS/1 READY MCU=STM32F429ZI"


def wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    """Poll ``predicate`` until it becomes true or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FakeRestsDevice:
    """Fake Renode TCP socket terminal + RESTS firmware protocol.

    It speaks exactly what ``Core/Src/main.c`` speaks: CRLF replies to
    ``HELLO?``, ``PING``, ``LANG:...`` and the two PTT commands, and
    ``ERR:UNKNOWN_CMD`` for anything else.  Every received frame (with
    its terminator) is recorded so tests can assert CRLF framing and
    command ordering.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        *,
        ready_message: str = READY_MESSAGE,
        close_after_first_command: bool = False,
    ) -> None:
        self.ready_message = ready_message
        self.close_after_first_command = close_after_first_command
        self.commands: List[str] = []
        self.frames: List[bytes] = []
        self.raw = bytearray()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._connections: List[socket.socket] = []

        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, 0))
        self._server.listen(1)
        self.host, self.port = self._server.getsockname()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    # -- server -----------------------------------------------------------
    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                break
            self._connections.append(conn)
            handler = threading.Thread(target=self._handle, args=(conn,), daemon=True)
            handler.start()

    def _handle(self, conn: socket.socket) -> None:
        buffer = b""
        try:
            while not self._closed.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    break
                with self._lock:
                    self.raw += chunk
                buffer += chunk
                while b"\n" in buffer:
                    raw_frame, buffer = buffer.split(b"\n", 1)
                    frame = raw_frame + b"\n"
                    self._record(frame)
                    reply = self._reply_for(raw_frame)
                    if reply:
                        try:
                            conn.sendall(reply)
                        except OSError:
                            return
                    if self.close_after_first_command:
                        return
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _record(self, frame: bytes) -> None:
        with self._lock:
            self.frames.append(frame)
            self.commands.append(frame.decode("ascii").rstrip("\r\n"))

    def _reply_for(self, raw_frame: bytes) -> Optional[bytes]:
        command = raw_frame.decode("ascii").rstrip("\r\n")
        if command == "HELLO?":
            return f"{self.ready_message}\r\n".encode("ascii")
        if command == "PING":
            return b"PONG\r\n"
        if command.startswith("LANG:"):
            return b"ACK:LANG\r\n"
        if command == "PTT:BEGIN":
            return b"ACK:PTT:BEGIN\r\n"
        if command == "PTT:END":
            return b"ACK:PTT:END\r\n"
        return b"ERR:UNKNOWN_CMD\r\n"

    # -- assertions helpers ----------------------------------------------
    def wait_for(self, command: str, timeout: float = 3.0) -> bool:
        return wait_until(lambda: command in self.commands, timeout=timeout)

    def index_of(self, command: str) -> int:
        try:
            return self.commands.index(command)
        except ValueError:
            return -1

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._server.close()
        except OSError:
            pass
        for conn in self._connections:
            try:
                conn.close()
            except OSError:
                pass
        self._thread.join(timeout=1.0)


class FakeMicrophoneStream:
    """Stand-in for ``sounddevice.InputStream`` driven by the test."""

    def __init__(self, callback, blocks=None, **_kwargs) -> None:
        self.callback = callback
        self.blocks = blocks if blocks is not None else [[0.0, 0.25, -0.25, 0.5]]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def pump(self) -> None:
        for index, block in enumerate(self.blocks):
            self.callback(list(block), len(block), None, None)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class FakeAsr:
    """Fake recogniser that records the file it was given."""

    def __init__(self, text="hello world", language="en", ok=True, error="", exc=None):
        self.text = text
        self.language = language
        self.ok = ok
        self.error = error
        self.exc = exc
        self.seen_path: Optional[str] = None
        self.existed_during_call = False
        self.calls = 0

    def transcribe(self, audio_path: str):
        from rests_host.asr import Transcript

        self.calls += 1
        self.seen_path = audio_path
        self.existed_during_call = Path(audio_path).is_file()
        if self.exc is not None:
            raise self.exc
        if not self.ok:
            return Transcript.failure(self.error)
        return Transcript(text=self.text, language=self.language, ok=True)
