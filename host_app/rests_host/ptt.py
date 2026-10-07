"""Push-to-talk sequencing for the RESTS firmware.

Press order (firmware expects the language before the session starts)::

    LANG:<source>:<target>
    PTT:BEGIN

Release order::

    PTT:END

Both press and release are idempotent: key auto-repeat and overlapping
button/key events must not produce a second ``PTT:BEGIN`` without a
matching ``PTT:END``.  Commands are handed to the UART link, which queues
them on its own TX thread - no socket work happens on the UI thread.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional, Tuple

from .languages import Language, lang_command
from .uart import RenodeUart

PTT_BEGIN = "PTT:BEGIN"
PTT_END = "PTT:END"

LanguageProvider = Callable[[], Tuple[Language, Language]]


class PttController:
    """Serialises LANG / PTT command pairs for press and release events."""

    def __init__(
        self,
        uart: RenodeUart,
        language_provider: LanguageProvider,
        *,
        on_event: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._uart = uart
        self._language_provider = language_provider
        self._on_event = on_event
        self._active = False
        self._lock = threading.Lock()
        self._session = 0
        self._last_source: Optional[Language] = None
        self._last_target: Optional[Language] = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def session(self) -> int:
        """Number of successfully started PTT sessions."""
        return self._session

    @property
    def last_pair(self) -> Optional[Tuple[Language, Language]]:
        """Language pair used by the most recent press."""
        return self._last_source, self._last_target

    def press(self) -> bool:
        """Start a PTT session. Returns ``True`` when BEGIN was queued."""
        with self._lock:
            if self._active:
                return False
            source, target = self._language_provider()
            try:
                command = lang_command(source, target)
            except ValueError:
                self._emit(f"invalid language pair: {source.key}->{target.key}")
                return False
            if not self._uart.send_command(command):
                self._emit("LANG not sent (UART not connected)")
                return False
            if not self._uart.send_command(PTT_BEGIN):
                self._emit("PTT:BEGIN not sent (UART not connected)")
                return False
            self._active = True
            self._session += 1
            self._last_source = source
            self._last_target = target
            self._emit(f"PTT session {self._session} started: {command}, {PTT_BEGIN}")
            return True

    def release(self) -> bool:
        """End the PTT session. Returns ``True`` when END was queued."""
        with self._lock:
            if not self._active:
                return False
            self._active = False
            queued = self._uart.send_command(PTT_END)
            if queued:
                self._emit(f"PTT session {self._session} ended: {PTT_END}")
            else:
                self._emit("PTT:END not sent (UART not connected)")
            return queued

    def force_release(self) -> bool:
        """Release even if state looks inconsistent (window focus loss)."""
        return self.release()

    def _emit(self, message: str) -> None:
        if self._on_event is not None:
            try:
                self._on_event(message)
            except Exception:  # pragma: no cover - UI callbacks must not break PTT
                pass
