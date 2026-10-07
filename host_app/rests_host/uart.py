"""TCP link to the RESTS firmware running in Renode.

Renode exposes the emulated ``sysbus.usart1`` as a plain TCP server
(``emulation CreateServerSocketTerminal 12345 "term" false``); this module
is the client side.  Everything that touches the network runs on two
private threads (one TX worker draining a FIFO queue, one RX worker
splitting CRLF replies), so the Tk UI thread never blocks on a socket.

Wire format: commands are ASCII terminated with CRLF (``\\r\\n``); the
firmware terminates on CR or LF, so a dropped LF (seen with some Renode
terminals) still leaves a valid line.  Replies are ASCII lines terminated
with CRLF.
"""

from __future__ import annotations

import enum
import queue
import socket
import threading
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 12345

HELLO_COMMAND = "HELLO?"
READY_PREFIX = "RESTS/1"
LINE_ENDING = "\r\n"
ERROR_PREFIX = "ERR:"

_STATE_CALLBACK = Callable[["StateEvent"], None]
_REPLY_CALLBACK = Callable[["ReplyEvent"], None]


class ConnectionState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    READY = "ready"
    ERROR = "error"


@dataclass(frozen=True)
class StateEvent:
    state: ConnectionState
    detail: str = ""


@dataclass(frozen=True)
class ReplyEvent:
    line: str

    @property
    def is_error(self) -> bool:
        return self.line.startswith(ERROR_PREFIX)


class UartError(RuntimeError):
    """Raised for programmer level misuse of the UART link."""


def _default_socket_factory(host: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((host, port), timeout=timeout)


class RenodeUart:
    """CRLF framed command channel to the emulated USART1."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        *,
        on_state: Optional[_STATE_CALLBACK] = None,
        on_reply: Optional[_REPLY_CALLBACK] = None,
        connect_timeout: float = 5.0,
        handshake_timeout: float = 5.0,
        socket_factory: Callable[[str, int, float], socket.socket] = _default_socket_factory,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.handshake_timeout = handshake_timeout
        self._on_state = on_state
        self._on_reply = on_reply
        self._socket_factory = socket_factory

        self._socket: Optional[socket.socket] = None
        self._tx_queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._tx_thread: Optional[threading.Thread] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._handshake_done = threading.Event()
        self._handshake_failed = threading.Event()
        self._lock = threading.Lock()
        self._state = ConnectionState.DISCONNECTED
        self._state_detail = ""
        self._closed = True

    # -- state ------------------------------------------------------------
    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def state_detail(self) -> str:
        return self._state_detail

    @property
    def is_ready(self) -> bool:
        return self._state is ConnectionState.READY

    def _set_state(self, state: ConnectionState, detail: str = "") -> None:
        with self._lock:
            self._state = state
            self._state_detail = detail
        if self._on_state is not None:
            try:
                self._on_state(StateEvent(state, detail))
            except Exception:  # pragma: no cover - UI callback must not kill I/O
                pass

    # -- lifecycle --------------------------------------------------------
    def connect(self) -> bool:
        """Open the socket, start the workers and perform ``HELLO?``.

        Returns ``True`` when the firmware answered with its greeting.
        Failures are reported through the state callback and never raise.
        """
        self.close()
        self._stop.clear()
        self._closed = False
        self._handshake_done.clear()
        self._handshake_failed.clear()
        self._set_state(ConnectionState.CONNECTING, f"{self.host}:{self.port}")

        try:
            sock = self._socket_factory(self.host, self.port, self.connect_timeout)
        except OSError as exc:
            self._set_state(ConnectionState.ERROR, f"connect failed: {exc}")
            self._closed = True
            return False

        try:
            sock.settimeout(None)
        except OSError:  # pragma: no cover - platform specific
            pass

        self._socket = sock
        self._tx_thread = threading.Thread(
            target=self._tx_worker, name="rests-uart-tx", daemon=True
        )
        self._rx_thread = threading.Thread(
            target=self._rx_worker, name="rests-uart-rx", daemon=True
        )
        self._tx_thread.start()
        self._rx_thread.start()

        self._set_state(ConnectionState.HANDSHAKING, HELLO_COMMAND)
        if not self.send_command(HELLO_COMMAND):
            self._set_state(ConnectionState.ERROR, "could not queue handshake")
            return False

        finished = self._handshake_done.wait(self.handshake_timeout)
        if finished and not self._handshake_failed.is_set():
            return True

        detail = "handshake timeout" if not self._handshake_failed.is_set() else "handshake failed"
        self._set_state(ConnectionState.ERROR, detail)
        self.close()
        return False

    def close(self) -> None:
        """Stop the workers and close the socket (idempotent)."""
        self._closed = True
        self._stop.set()
        self._tx_queue.put(None)
        sock, self._socket = self._socket, None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        for thread in (self._tx_thread, self._rx_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
        self._tx_thread = None
        self._rx_thread = None
        while True:
            try:
                item = self._tx_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:  # pragma: no cover - sentinel just dropped
                continue
        with self._lock:
            state = self._state
        if state is not ConnectionState.ERROR:
            self._set_state(ConnectionState.DISCONNECTED)

    # -- sending ----------------------------------------------------------
    def send_command(self, command: str) -> bool:
        """Queue a command; ``True`` when it was accepted for transmission.

        The write itself happens on the TX worker thread, so callers (the
        Tk event loop included) never touch the socket.
        """
        text = command.strip()
        if not text:
            raise UartError("command must not be empty")
        if any(ch in text for ch in ("\r", "\n")):
            raise UartError("command must not contain CR or LF")
        if self._socket is None or self._closed:
            return False
        self._tx_queue.put(text)
        return True

    def _tx_worker(self) -> None:
        while not self._stop.is_set():
            item = self._tx_queue.get()
            if item is None:
                break
            sock = self._socket
            if sock is None:
                continue
            try:
                sock.sendall((item + LINE_ENDING).encode("ascii", errors="strict"))
            except OSError:
                if not self._stop.is_set():
                    self._handshake_failed.set()
                    self._set_state(ConnectionState.ERROR, "socket write failed")
                break

    # -- receiving --------------------------------------------------------
    def _rx_worker(self) -> None:
        buffer = b""
        sock = self._socket
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("ascii", errors="replace").rstrip("\r")
                if line:
                    self._handle_line(line)
        if not self._stop.is_set():
            if not self._handshake_done.is_set():
                self._handshake_failed.set()
                self._set_state(ConnectionState.ERROR, "connection closed during handshake")
            else:
                self._set_state(ConnectionState.DISCONNECTED, "remote closed the connection")

    def _handle_line(self, line: str) -> None:
        if (
            self._state is ConnectionState.HANDSHAKING
            and line.startswith(READY_PREFIX)
            and not self._handshake_done.is_set()
        ):
            self._handshake_done.set()
            self._set_state(ConnectionState.READY, line)
        if self._on_reply is not None:
            try:
                self._on_reply(ReplyEvent(line))
            except Exception:  # pragma: no cover - UI callback must not kill I/O
                pass
