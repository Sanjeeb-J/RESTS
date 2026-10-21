"""TCP link to the RESTS firmware running in Renode.

Renode exposes the emulated ``sysbus.usart1`` as a plain TCP server; this
module is the client side. Network work runs on private TX/RX threads so
the Tk event loop never blocks. The byte stream multiplexes newline-ended
ASCII control commands with CRC-checked binary audio frames. Firmware
echoes each valid audio frame for host-side verification.
"""

from __future__ import annotations

import enum
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from .audio_transport import (
    FRAME_TYPE_AUDIO,
    FRAME_HEADER,
    MAGIC,
    MAX_PAYLOAD,
    AudioTransportError,
    decode_frame,
    roundtrip_audio,
)

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


@dataclass
class _TxRequest:
    data: bytes
    sent: Optional[threading.Event] = None
    error: Optional[OSError] = None


def _default_socket_factory(host: str, port: int, timeout: float) -> socket.socket:
    return socket.create_connection((host, port), timeout=timeout)


class RenodeUart:
    """Text commands and framed audio relay over emulated USART1."""

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
        self._tx_queue: "queue.Queue[Optional[_TxRequest]]" = queue.Queue()
        self._tx_thread: Optional[threading.Thread] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._handshake_done = threading.Event()
        self._handshake_failed = threading.Event()
        self._lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._audio_waiters: dict[int, "queue.Queue[object]"] = {}
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
            # A short timeout lets the RX worker recover from an incomplete
            # binary frame without treating an idle connection as closed.
            sock.settimeout(0.2)
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
        self._tx_queue.put(
            _TxRequest((text + LINE_ENDING).encode("ascii", errors="strict"))
        )
        return True

    def relay_audio(self, samples, *, timeout: float = 3.0) -> list[float]:
        """Round-trip compressed audio through the connected MCU UART relay."""
        if not self.is_ready:
            raise UartError("Renode UART is not connected; audio cannot be relayed")

        def exchange(frame: bytes, sequence: int) -> bytes:
            waiter: "queue.Queue[object]" = queue.Queue(maxsize=1)
            with self._audio_lock:
                if sequence in self._audio_waiters:
                    raise UartError(f"audio sequence {sequence} is already pending")
                self._audio_waiters[sequence] = waiter
            sent = threading.Event()
            request = _TxRequest(frame, sent=sent)
            try:
                self._tx_queue.put(request)
                if not sent.wait(timeout):
                    raise UartError("timed out queuing audio packet to Renode")
                if request.error is not None:
                    raise UartError(f"could not send audio packet: {request.error}")
                try:
                    reply = waiter.get(timeout=timeout)
                except queue.Empty as exc:
                    raise UartError(
                        f"timed out waiting for STM32 audio packet {sequence}"
                    ) from exc
                if isinstance(reply, BaseException):
                    raise UartError(f"invalid STM32 audio reply: {reply}") from reply
                return reply  # type: ignore[return-value]
            finally:
                with self._audio_lock:
                    self._audio_waiters.pop(sequence, None)

        try:
            return roundtrip_audio(samples, exchange)
        except AudioTransportError as exc:
            raise UartError(str(exc)) from exc

    def _tx_worker(self) -> None:
        while not self._stop.is_set():
            item = self._tx_queue.get()
            if item is None:
                break
            sock = self._socket
            if sock is None:
                if item.sent is not None:
                    item.error = OSError("UART socket is closed")
                    item.sent.set()
                continue
            try:
                sock.sendall(item.data)
            except OSError as exc:
                item.error = exc
                if item.sent is not None:
                    item.sent.set()
                if not self._stop.is_set():
                    self._handshake_failed.set()
                    self._set_state(ConnectionState.ERROR, "socket write failed")
                break
            if item.sent is not None:
                item.sent.set()

    # -- receiving --------------------------------------------------------
    def _rx_worker(self) -> None:
        wire = bytearray()
        lines = bytearray()
        incomplete_since: Optional[float] = None
        sock = self._socket
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                if incomplete_since is not None:
                    if time.monotonic() - incomplete_since > 2.0:
                        # Drop the stale magic byte and resume line parsing.
                        if wire:
                            self._consume_lines(lines, bytes(wire[:1]))
                            del wire[:1]
                        incomplete_since = None
                continue
            except OSError:
                break
            if not chunk:
                break
            wire.extend(chunk)
            while wire:
                marker = wire.find(MAGIC)
                if marker == 0:
                    if len(wire) < FRAME_HEADER.size:
                        incomplete_since = incomplete_since or time.monotonic()
                        break
                    _magic, kind, sequence, length = FRAME_HEADER.unpack_from(wire)
                    if kind != FRAME_TYPE_AUDIO or not 0 < length <= MAX_PAYLOAD:
                        self._consume_lines(lines, bytes(wire[:1]))
                        del wire[:1]
                        incomplete_since = None
                        continue
                    total = FRAME_HEADER.size + length + 2
                    if len(wire) < total:
                        incomplete_since = incomplete_since or time.monotonic()
                        break
                    frame = bytes(wire[:total])
                    del wire[:total]
                    incomplete_since = None
                    try:
                        frame_sequence, _payload = decode_frame(frame)
                    except AudioTransportError as exc:
                        self._dispatch_audio(frame_sequence=sequence, result=exc)
                    else:
                        self._dispatch_audio(frame_sequence=frame_sequence, result=frame)
                    continue
                if marker > 0:
                    self._consume_lines(lines, bytes(wire[:marker]))
                    del wire[:marker]
                    incomplete_since = None
                    continue
                # Keep a possible first magic byte at the end of the stream.
                keep = 1 if wire[-1] == MAGIC[0] else 0
                if len(wire) > keep:
                    count = len(wire) - keep
                    self._consume_lines(lines, bytes(wire[:count]))
                    del wire[:count]
                break
        if not self._stop.is_set():
            if not self._handshake_done.is_set():
                self._handshake_failed.set()
                self._set_state(ConnectionState.ERROR, "connection closed during handshake")
            else:
                self._set_state(ConnectionState.DISCONNECTED, "remote closed the connection")

    def _consume_lines(self, buffer: bytearray, data: bytes) -> None:
        buffer.extend(data)
        while b"\n" in buffer:
            raw, remainder = buffer.split(b"\n", 1)
            buffer[:] = remainder
            line = raw.decode("ascii", errors="replace").rstrip("\r")
            if line:
                self._handle_line(line)
        if len(buffer) > 8192:
            buffer.clear()

    def _dispatch_audio(self, *, frame_sequence: int, result: object) -> None:
        with self._audio_lock:
            waiter = self._audio_waiters.get(frame_sequence)
        if waiter is not None:
            try:
                waiter.put_nowait(result)
            except queue.Full:  # pragma: no cover - duplicate MCU reply
                pass

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
