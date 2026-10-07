"""Tkinter desktop UI for the RESTS host application.

Threading model:

* **UI thread** - Tk event loop only; never blocks on sockets, audio or ML.
* **UART TX/RX threads** - owned by :class:`rests_host.uart.RenodeUart`.
* **Job worker thread** - transcription, translation and spoken output.
* **Model loader thread** - warms the local models after start-up.

Anything that must touch a widget is posted through ``self._ui_queue``
and drained by ``after()``.

Layout - section frames, palette, fonts and the caption panels live in
:mod:`rests_host.ui_theme`; this module wires them to the pipeline.  Top to
bottom: connection, language and output, push to talk, source transcript,
translated caption (the headline, ~2/3 of the caption area), an optional
error/warning strip and the status bar.  The window refuses to shrink below
700x660 so every section stays usable, and a ``minsize`` on the caption rows
keeps the ~1:2 split at any size.
"""

from __future__ import annotations

import queue
import threading
from typing import Callable, Optional, Tuple

import tkinter as tk
from tkinter import ttk

from .asr import ASR_DEVICES, AsrUnavailableError, FasterWhisperAsr
from .audio import MicrophoneRecorder, RecorderError, is_long_enough
from .config import AppConfig
from .languages import (
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
    Language,
    lang_command,
    language_by_label,
    source_labels,
    target_labels,
)
from .pipeline import ProcessingResult, process_recording
from .ptt import PttController
from .translate import TranslationBackend, TranslationService, create_backend
from .tts import SpeechStatus, WindowsSpeech
from .uart import ConnectionState, ReplyEvent, RenodeUart, StateEvent
from .ui_theme import (
    FONTS,
    PALETTE,
    SEVERITY_COLORS,
    apply_theme,
    caption_panel,
    classify_status,
    make_card,
    shorten,
)

POLL_MS = 50

#: Device combo box label -> ``asr_device`` value.
_DEVICE_LABELS = {"auto": "Auto", "cpu": "CPU", "cuda": "CUDA"}
_DEVICE_BY_LABEL = {label: key for key, label in _DEVICE_LABELS.items()}

#: Connection pill colours (white text on a solid, colour-blind-safe hue).
_STATE_COLORS = {
    ConnectionState.DISCONNECTED: "#6B7480",
    ConnectionState.CONNECTING: PALETTE["warn"],
    ConnectionState.HANDSHAKING: PALETTE["warn"],
    ConnectionState.READY: PALETTE["ok"],
    ConnectionState.ERROR: PALETTE["error"],
}

#: Push-to-talk button appearance per state (ready / listening / processing).
_PTT_BACKGROUNDS = {
    "ready": PALETTE["accent"],
    "listening": PALETTE["error"],
    "processing": PALETTE["warn"],
}
_PTT_ACTIVE = {
    "ready": PALETTE["accent_dark"],
    "listening": "#8C1D18",
    "processing": "#6E4100",
}


class RestsApp(tk.Tk):
    """Main window."""

    def __init__(
        self,
        config: Optional[AppConfig] = None,
        *,
        uart_factory: Callable[..., RenodeUart] = RenodeUart,
        recorder_factory: Callable[..., MicrophoneRecorder] = MicrophoneRecorder,
        asr_factory: Callable[..., FasterWhisperAsr] = FasterWhisperAsr,
        speech_factory: Callable[[], WindowsSpeech] = WindowsSpeech,
        backend_factory: Callable[[], TranslationBackend] = create_backend,
        auto_connect: bool = True,
    ) -> None:
        super().__init__()
        self.config_obj = config or AppConfig.from_env()
        apply_theme(self)
        self.title("RESTS Host - STM32F429")
        self._center_initial_geometry()
        self.minsize(700, 660)

        self._ui_queue: "queue.Queue[Tuple[str, object]]" = queue.Queue()
        self._jobs: "queue.Queue[Optional[Tuple[list, Language, Language]]]" = queue.Queue()
        self._closing = False
        self._recording = False
        self._key_held = False
        self._busy = False
        self._ptt_state = "ready"
        self._status_level = "info"
        self._status_transient = True
        self._notice_visible = False
        self._source_lang: Language = SOURCE_LANGUAGES[0]
        self._target_lang: Language = TARGET_LANGUAGES[0]

        self.asr = asr_factory(device=self.config_obj.asr_device)
        self.translation = TranslationService(backend_factory())
        self.speech = speech_factory()
        self.recorder = recorder_factory(sample_rate=self.config_obj.sample_rate)
        self.uart = uart_factory(
            self.config_obj.host,
            self.config_obj.port,
            on_state=self._on_uart_state,
            on_reply=self._on_uart_reply,
        )
        self.ptt = PttController(
            self.uart,
            self._current_pair,
            on_event=self._post_status,
        )

        self._build_ui()
        self._bind_shortcut()

        self._worker = threading.Thread(
            target=self._job_worker, name="rests-jobs", daemon=True
        )
        self._worker.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll_queue)
        self.after(150, self._preload_models)
        if auto_connect:
            self.after(400, self.connect_uart)

    # ------------------------------------------------------------------ UI
    def _center_initial_geometry(self) -> None:
        """Sensible default size, clamped so small screens stay usable."""
        width = min(960, max(700, self.winfo_screenwidth() - 120))
        height = min(780, max(660, self.winfo_screenheight() - 160))
        x = max((self.winfo_screenwidth() - width) // 2, 0)
        y = max((self.winfo_screenheight() - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _field_label(parent: tk.Misc, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            font=FONTS["label"],
            fg=PALETTE["muted"],
            bg=PALETTE["card"],
            anchor="w",
        )

    def _build_ui(self) -> None:
        """Sections: connection, language settings, PTT, captions, status."""
        shell = tk.Frame(self, bg=PALETTE["window"], padx=14, pady=12)
        shell.pack(fill="both", expand=True)
        shell.grid_columnconfigure(0, weight=1)
        shell.grid_rowconfigure(3, weight=1)  # captions grow, the rest do not

        # -- section: connection -----------------------------------------
        conn_card, conn = make_card(shell, "Connection")
        conn_card.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        conn.grid_columnconfigure(1, weight=1)

        self._host_var = tk.StringVar(value=self.config_obj.host)
        self._port_var = tk.StringVar(value=str(self.config_obj.port))

        self._field_label(conn, "Host").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(conn, textvariable=self._host_var, width=22).grid(
            row=0, column=1, sticky="ew", padx=(0, 16)
        )
        self._field_label(conn, "Port").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(conn, textvariable=self._port_var, width=7).grid(
            row=0, column=3, sticky="w", padx=(0, 16)
        )
        self._connect_btn = ttk.Button(conn, text="Connect", command=self.connect_uart)
        self._connect_btn.grid(row=0, column=4, padx=(0, 12))
        self._state_lbl = tk.Label(
            conn,
            text="disconnected",
            fg="white",
            bg=_STATE_COLORS[ConnectionState.DISCONNECTED],
            font=FONTS["pill"],
            padx=10,
            pady=3,
        )
        self._state_lbl.grid(row=0, column=5, sticky="w")
        self._state_detail_lbl = tk.Label(
            conn,
            text="",
            fg=PALETTE["muted"],
            bg=PALETTE["card"],
            font=FONTS["small"],
            anchor="w",
            justify="left",
        )
        self._state_detail_lbl.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        conn.bind(
            "<Configure>",
            lambda event: self._state_detail_lbl.configure(
                wraplength=max(200, event.width - 32)
            ),
        )

        # -- section: language & output -----------------------------------
        lang_card, lang = make_card(shell, "Language and output")
        lang_card.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        lang.grid_columnconfigure(6, weight=1)

        self._source_var = tk.StringVar(value=SOURCE_LANGUAGES[0].label)
        self._target_var = tk.StringVar(value=TARGET_LANGUAGES[0].label)
        self._device_var = tk.StringVar(
            value=_DEVICE_LABELS.get(self.config_obj.asr_device, "Auto")
        )
        self._speech_var = tk.BooleanVar(value=self.config_obj.speech_enabled)
        # Plain mirror: the job worker thread must not read Tk variables
        # ("main thread is not in main loop" would kill the worker).
        self._speech_enabled = bool(self.config_obj.speech_enabled)

        self._field_label(lang, "Source").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._source_box = ttk.Combobox(
            lang,
            textvariable=self._source_var,
            values=list(source_labels()),
            state="readonly",
            width=18,
        )
        self._source_box.grid(row=0, column=1, sticky="w", padx=(0, 16))
        self._source_box.bind("<<ComboboxSelected>>", self._on_language_changed)

        self._field_label(lang, "Target").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self._target_box = ttk.Combobox(
            lang,
            textvariable=self._target_var,
            values=list(target_labels()),
            state="readonly",
            width=18,
        )
        self._target_box.grid(row=0, column=3, sticky="w", padx=(0, 16))
        self._target_box.bind("<<ComboboxSelected>>", self._on_language_changed)

        self._field_label(lang, "Device").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self._device_box = ttk.Combobox(
            lang,
            textvariable=self._device_var,
            values=list(_DEVICE_LABELS.values()),
            state="readonly",
            width=7,
        )
        self._device_box.grid(row=0, column=5, sticky="w", padx=(0, 16))
        self._device_box.bind("<<ComboboxSelected>>", self._on_device_changed)

        self._speech_check = ttk.Checkbutton(
            lang,
            text="Spoken output",
            variable=self._speech_var,
            command=self._on_speech_toggled,
        )
        self._speech_check.grid(row=0, column=6, sticky="w")

        self._speech_lbl = tk.Label(
            lang,
            text="",
            bg=PALETTE["card"],
            fg=PALETTE["muted"],
            font=FONTS["body"],
            anchor="w",
            justify="left",
        )
        self._speech_lbl.grid(row=1, column=0, columnspan=7, sticky="ew", pady=(8, 0))
        lang.bind(
            "<Configure>",
            lambda event: self._speech_lbl.configure(
                wraplength=max(160, event.width - 32)
            ),
        )

        # -- section: push to talk ----------------------------------------
        ptt_card, ptt = make_card(shell, "Push to talk")
        ptt_card.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        self._ptt_btn = tk.Button(
            ptt,
            text=self._ptt_label("ready"),
            font=FONTS["ptt"],
            fg="white",
            bg=_PTT_BACKGROUNDS["ready"],
            activebackground=_PTT_ACTIVE["ready"],
            activeforeground="white",
            relief="flat",
            bd=0,
            highlightthickness=0,
            pady=10,
            cursor="hand2",
        )
        self._ptt_btn.pack(fill="x", pady=(2, 8))
        self._ptt_btn.bind("<ButtonPress-1>", self._on_button_press)
        self._ptt_btn.bind("<ButtonRelease-1>", self._on_button_release)
        self._ptt_btn.bind("<Leave>", self._on_button_leave)
        tk.Label(
            ptt,
            text=(
                f"Press and hold the button (or {self.config_obj.ptt_key}); release to "
                "stop. Commands: LANG:<src>:<tgt>, PTT:BEGIN / PTT:END."
            ),
            font=FONTS["small"],
            fg=PALETTE["muted"],
            bg=PALETTE["card"],
            anchor="w",
            justify="left",
        ).pack(fill="x")

        # -- section: captions --------------------------------------------
        captions = tk.Frame(shell, bg=PALETTE["window"])
        captions.grid(row=3, column=0, sticky="nsew", pady=(0, 6))
        captions.grid_rowconfigure(0, weight=1)
        captions.grid_rowconfigure(1, weight=2)  # the translation is the headline
        captions.grid_columnconfigure(0, weight=1)

        source_shell, self._source_text = caption_panel(
            captions,
            "Source transcript",
            background=PALETTE["source_bg"],
            foreground=PALETTE["source_fg"],
            font=FONTS["caption"],
            placeholder="Press and hold to talk - the transcript appears here.",
        )
        source_shell.grid(row=0, column=0, sticky="nsew", pady=(0, 6))

        target_shell, self._target_text = caption_panel(
            captions,
            "Translated caption",
            background=PALETTE["target_bg"],
            foreground=PALETTE["target_fg"],
            font=FONTS["caption_big"],
            placeholder="The translated caption appears here.",
            accent=True,
        )
        target_shell.grid(row=1, column=0, sticky="nsew")

        def _balance_caption_rows(event) -> None:
            # Keep a ~1:2 split even when the two panels' requested sizes
            # no longer fit: grid honours `minsize` where weight alone would
            # let the first panel consume the whole area.
            captions.grid_rowconfigure(0, minsize=int(event.height * 0.30))
            captions.grid_rowconfigure(1, minsize=int(event.height * 0.66))

        captions.bind("<Configure>", _balance_caption_rows)
        self._set_caption(self._source_text, "")
        self._set_caption(self._target_text, "")

        # -- sticky notice strip (errors / warnings only, hidden by default)
        self._notice_bar = tk.Frame(
            shell,
            bg=PALETTE["warn_soft"],
            highlightbackground=PALETTE["warn"],
            highlightthickness=1,
            padx=12,
            pady=8,
        )
        self._notice_lbl = tk.Label(
            self._notice_bar,
            text="",
            bg=PALETTE["warn_soft"],
            fg=PALETTE["text"],
            font=FONTS["body"],
            anchor="w",
            justify="left",
        )
        self._notice_lbl.pack(side="left", fill="both", expand=True)
        self._notice_btn = tk.Button(
            self._notice_bar,
            text="Dismiss",
            font=FONTS["small"],
            relief="flat",
            bd=0,
            bg=PALETTE["warn_soft"],
            fg=PALETTE["muted"],
            activebackground=PALETTE["warn_soft"],
            activeforeground=PALETTE["text"],
            command=self._clear_notice,
        )
        self._notice_btn.pack(side="right", padx=(12, 0))
        self._notice_bar.bind(
            "<Configure>",
            lambda event: self._notice_lbl.configure(
                wraplength=max(200, event.width - 110)
            ),
        )
        self._notice_bar.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self._notice_bar.grid_remove()

        # -- status bar ----------------------------------------------------
        status = tk.Frame(
            shell,
            bg=PALETTE["card"],
            highlightbackground=PALETTE["border"],
            highlightthickness=1,
            padx=12,
            pady=7,
        )
        # The status bar always keeps its height, even in a squeezed window.
        shell.grid_rowconfigure(5, minsize=30)
        status.grid(row=5, column=0, sticky="ew")
        self._status_dot = tk.Label(
            status, text="●", bg=PALETTE["card"], fg=SEVERITY_COLORS["info"], font=FONTS["status"]
        )
        self._status_dot.pack(side="left", padx=(0, 8))
        self._status_lbl = tk.Label(
            status,
            text="Ready.",
            bg=PALETTE["card"],
            fg=SEVERITY_COLORS["info"],
            font=FONTS["status"],
            anchor="w",
            justify="left",
        )
        self._status_lbl.pack(side="left", fill="both", expand=True)
        status.bind(
            "<Configure>",
            lambda event: self._status_lbl.configure(
                wraplength=max(200, event.width - 40)
            ),
        )
        self._set_status("Ready.", "ok")

        def _balance_sections(event) -> None:
            # Rows with weight 0 (connection, language, PTT, status) always
            # keep their requested height, so the captions row is guarded by
            # an explicit minimum: a squeezed window would otherwise hand it
            # only a few pixels and both panels would render empty.
            shell.grid_rowconfigure(3, minsize=max(140, int(event.height * 0.22)))

        shell.bind("<Configure>", _balance_sections)

    def _bind_shortcut(self) -> None:
        key = self.config_obj.ptt_key
        self.bind(f"<KeyPress-{key}>", self._on_key_press)
        self.bind(f"<KeyRelease-{key}>", self._on_key_release)
        self.bind("<FocusOut>", self._on_focus_out)

    # ---------------------------------------------------------- appearance
    def _ptt_label(self, state: str) -> str:
        if state == "listening":
            return "\u25cf LISTENING - release to stop"
        if state == "processing":
            return "\u25cf PROCESSING - one moment"
        return f"HOLD TO TALK  (hold {self.config_obj.ptt_key})"

    def _set_ptt_state(self, state: str) -> None:
        """Ready / listening / processing: high contrast, hard to miss."""
        self._ptt_state = state
        self._ptt_btn.configure(
            text=self._ptt_label(state),
            bg=_PTT_BACKGROUNDS.get(state, _PTT_BACKGROUNDS["ready"]),
            activebackground=_PTT_ACTIVE.get(state, _PTT_ACTIVE["ready"]),
        )

    def _set_status(
        self, message: str, level: Optional[str] = None, *, transient: bool = True
    ) -> None:
        """Colour-coded status line; ``transient=False`` keeps the message up."""
        resolved = level or classify_status(message)
        if transient and resolved == "info" and self._status_transient is False:
            return  # background chatter never erases a message we keep
        self._status_level = resolved
        self._status_transient = transient
        color = SEVERITY_COLORS.get(resolved, PALETTE["muted"])
        self._status_lbl.configure(text=message, fg=color)
        self._status_dot.configure(fg=color)

    def _show_notice(self, message: str, level: str = "warn") -> None:
        """Sticky strip for errors/warnings so transient status cannot hide it."""
        if not message:
            return
        strong = PALETTE["error"] if level == "error" else PALETTE["warn"]
        soft = PALETTE["error_soft"] if level == "error" else PALETTE["warn_soft"]
        self._notice_bar.configure(bg=soft, highlightbackground=strong)
        self._notice_lbl.configure(text=message, bg=soft)
        self._notice_btn.configure(bg=soft, activebackground=soft, fg=strong)
        self._notice_bar.grid()  # restore the row/geometry from the build step
        self._notice_visible = True

    def _clear_notice(self) -> None:
        self._notice_bar.grid_remove()
        self._notice_visible = False

    def _set_speech_text(self, message: str, level: str = "info") -> None:
        self._speech_lbl.configure(
            text=message, fg=SEVERITY_COLORS.get(level, PALETTE["muted"])
        )

    # ------------------------------------------------------------ language
    def _current_pair(self) -> Tuple[Language, Language]:
        return self._source_lang, self._target_lang

    def _on_language_changed(self, _event=None) -> None:
        self._source_lang = language_by_label(self._source_var.get())
        self._target_lang = language_by_label(self._target_var.get())
        try:
            command = lang_command(self._source_lang, self._target_lang)
        except ValueError as exc:
            self._post_status(f"invalid language pair: {exc}")
            return
        if self.uart.is_ready:
            if self.uart.send_command(command):
                self._post_status(f"sent {command}")
            else:
                self._post_status(f"{command} not sent (UART not connected)")
        else:
            self._post_status(f"language set to {command} (not sent: UART not connected)")
        self._refresh_speech_status()

    def _on_speech_toggled(self) -> None:
        self._speech_enabled = bool(self._speech_var.get())
        self._refresh_speech_status()

    def _on_device_changed(self, _event=None) -> None:
        choice = _DEVICE_BY_LABEL.get(self._device_var.get(), "auto")
        if choice not in ASR_DEVICES:  # pragma: no cover - readonly combo box
            return
        try:
            self.asr.set_device(choice)
        except (ValueError, AttributeError) as exc:
            self._post_status(f"cannot change device: {exc}")
            return
        self._post_status(
            f"ASR device set to {choice} ({self.asr.device}, "
            f"{self.asr.compute_type}); models reload on the next recording"
        )

    def _refresh_speech_status(self) -> None:
        if not self._speech_var.get():
            self._set_speech_text("Spoken output disabled; captions only.", "info")
            return
        status: SpeechStatus = self.speech.status(self._target_lang)
        self._set_speech_text(status.detail, "ok" if status.available else "warn")

    # ------------------------------------------------------------- PTT/keys
    def _on_button_press(self, _event) -> str:
        self._start_ptt()
        return "break"

    def _on_button_release(self, _event) -> str:
        self._stop_ptt()
        return "break"

    def _on_button_leave(self, _event) -> None:
        if self._recording:
            self._stop_ptt()

    def _on_key_press(self, event) -> str:
        if self._key_held:
            return "break"  # ignore auto-repeat
        self._key_held = True
        self._start_ptt()
        return "break"

    def _on_key_release(self, _event) -> str:
        if not self._key_held:
            return "break"
        self._key_held = False
        self._stop_ptt()
        return "break"

    def _on_focus_out(self, _event) -> None:
        # Safety net: never leave the firmware stuck in a PTT session.
        if self._key_held or self._recording:
            self._key_held = False
            self._stop_ptt()

    def _start_ptt(self) -> None:
        if self._recording:
            return
        try:
            self.recorder.start()
        except RecorderError as exc:
            self._set_status(f"microphone error: {exc}", "error", transient=False)
            self._show_notice(f"microphone error: {exc}", "error")
            return
        self._recording = True
        self._set_ptt_state("listening")
        queued = self.ptt.press()
        if queued:
            self._set_status("PTT:BEGIN queued (LANG sent first)", "ok")
        else:
            self._set_status(
                "recording locally; UART not connected so no PTT:BEGIN", "warn"
            )

    def _stop_ptt(self) -> None:
        if not self._recording:
            return
        self._recording = False
        samples = self.recorder.stop()
        ended = self.ptt.release()
        self._set_ptt_state("ready")
        if ended:
            self._set_status("PTT:END queued", "ok")
        if not samples or not is_long_enough(len(samples), self.config_obj.sample_rate):
            self._set_status("recording ignored (too short)", "warn")
            return
        self._jobs.put((samples, self._source_lang, self._target_lang))
        self._set_ptt_state("processing")
        self._set_status("processing recording...", "busy")

    # --------------------------------------------------------------- UART
    def connect_uart(self) -> None:
        # Read the widgets on the UI thread, then do the blocking work
        # (socket + handshake) on a private thread.
        host = self._host_var.get().strip() or self.config_obj.host
        try:
            port = int(self._port_var.get())
        except ValueError:
            port = self.config_obj.port
        self._connect_btn.configure(state="disabled", text="Connecting...")
        threading.Thread(
            target=self._connect_worker,
            args=(host, port),
            name="rests-connect",
            daemon=True,
        ).start()

    def _connect_worker(self, host: str, port: int) -> None:
        try:
            self.uart.host = host
            self.uart.port = port
            ok = self.uart.connect()
            if ok:
                pair = self._current_pair()
                try:
                    self.uart.send_command(lang_command(*pair))
                except ValueError:
                    pass
                self._post_status(f"handshake OK on {self.uart.host}:{self.uart.port}")
            else:
                self._post_status(
                    f"connection failed: {self.uart.state_detail} "
                    "(is renode/host_app/renode/stm32f429_rests.resc running?)"
                )
        finally:
            self._post(("connect_button", None))

    def _on_uart_state(self, event: StateEvent) -> None:
        # Called from UART worker threads: only enqueue.
        self._post(("state", event))

    def _on_uart_reply(self, event: ReplyEvent) -> None:
        self._post(("reply", event))

    # --------------------------------------------------------------- jobs
    def _preload_models(self) -> None:
        def worker() -> None:
            try:
                self.asr.load()
                self._post_status(f"speech model ready: {self.asr.status()}", "info")
            except AsrUnavailableError as exc:
                self._post_status(str(exc))
            usable, detail = self.translation.available()
            if not usable:
                self._post_status(detail)
            self._refresh_speech_status_soon()

        threading.Thread(target=worker, name="rests-model-loader", daemon=True).start()

    def _refresh_speech_status_soon(self) -> None:
        self._post(("speech_status", None))

    def _job_worker(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                break
            samples, source, target = job
            self._post(("busy", True))
            try:
                result = process_recording(
                    samples,
                    self.asr,
                    self.translation,
                    source,
                    target,
                    sample_rate=self.config_obj.sample_rate,
                )
            except Exception as exc:  # pragma: no cover - defensive
                result = ProcessingResult(
                    source_text="", translated_text="", detected_language=None,
                    ok=False, error=str(exc),
                )
            self._post(("result", result))
            try:
                if (
                    result.ok
                    and result.translation_ok
                    and result.translated_text
                    and self._speech_enabled
                ):
                    status = self.speech.speak(result.translated_text, target)
                    if not status.available:
                        self._post(("speech", status))
            except Exception:  # best-effort TTS, never kill the worker
                pass
            self._post(("busy", False))

    # ------------------------------------------------------------ queue/ UI
    def _post(self, item: Tuple[str, object]) -> None:
        self._ui_queue.put(item)

    def _post_status(self, message: str, level: Optional[str] = None) -> None:
        self._post(("status", (message, level)))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                self._handle(kind, payload)
        except queue.Empty:
            pass
        if not self._closing:
            self.after(POLL_MS, self._poll_queue)

    def _handle(self, kind: str, payload: object) -> None:
        if kind == "status":
            message, level = (
                payload if isinstance(payload, tuple) else (payload, None)  # type: ignore[misc]
            )
            self._set_status(str(message), level)
        elif kind == "state":
            event = payload  # type: StateEvent
            color = _STATE_COLORS.get(event.state, PALETTE["muted"])
            self._state_lbl.configure(text=event.state.value, bg=color)
            self._state_detail_lbl.configure(text=event.detail)
            if event.state is ConnectionState.READY:
                self._connect_btn.configure(state="normal", text="Disconnect")
            elif event.state in (ConnectionState.DISCONNECTED, ConnectionState.ERROR):
                self._connect_btn.configure(state="normal", text="Connect")
        elif kind == "reply":
            event = payload  # type: ReplyEvent
            if event.is_error:
                self._set_status(
                    f"firmware error: {event.line}", "error", transient=False
                )
                self._show_notice(f"firmware error: {event.line}", "error")
        elif kind == "result":
            result = payload  # type: ProcessingResult
            self._apply_result(result)
        elif kind == "busy":
            self._busy = bool(payload)
            if self._busy:
                self._set_ptt_state("processing")
            else:
                if not self._recording:
                    self._set_ptt_state("ready")
                if self._status_transient and not self._recording:
                    self._set_status("Ready.", "ok")
        elif kind == "speech":
            status = payload  # type: SpeechStatus
            self._set_speech_text(status.detail, "ok" if status.available else "warn")
        elif kind == "speech_status":
            self._refresh_speech_status()
        elif kind == "connect_button":
            if self.uart.state is not ConnectionState.READY:
                self._connect_btn.configure(state="normal", text="Connect")

    def _apply_result(self, result: ProcessingResult) -> None:
        notice = str(result.asr_notice or "")
        if not result.ok:
            self._set_caption(self._source_text, f"[error] {result.error}")
            self._set_caption(self._target_text, "")
            self._set_status(
                shorten(f"processing failed: {result.error}"), "error", transient=False
            )
            self._show_notice(
                " | ".join(part for part in (result.error, notice) if part), "error"
            )
            return
        self._set_caption(self._source_text, result.source_text or "(no speech detected)")
        if result.translation_ok:
            self._set_caption(self._target_text, result.translated_text)
            self._set_status(
                f"translated via {self.translation.name} "
                f"({result.language_key or '?'} -> {result.target_key})",
                "ok",
                transient=False,
            )
        else:
            # Captions keep working: fall back to the source text.
            self._set_caption(self._target_text, result.source_text)
            self._set_status(
                shorten(f"translation unavailable: {result.translation_error}"),
                "warn",
                transient=False,
            )
            self._show_notice(
                " | ".join(
                    part for part in (result.translation_error, notice) if part
                ),
                "warn",
            )
            return
        # Success: surface a mid-session change (e.g. CUDA -> CPU) once.
        if notice:
            self._show_notice(notice, "warn")
        else:
            self._clear_notice()

    @staticmethod
    def _set_caption(widget: tk.Text, text: str) -> None:
        """Replace caption text (or show its muted placeholder when empty)."""
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if text and text.strip():
            tag = "error" if text.startswith("[error]") else "body"
            widget.insert("end", text, tag)
        else:
            placeholder = getattr(widget, "_rests_placeholder", "")
            if placeholder:
                widget.insert("end", placeholder, "hint")
        widget.see("1.0")
        widget.configure(state="disabled")

    # -------------------------------------------------------------- closing
    def _on_close(self) -> None:
        self._closing = True
        try:
            if self._recording:
                self._stop_ptt()
            if self.ptt.active:
                self.ptt.release()
        finally:
            self._jobs.put(None)
            threading.Thread(target=self.uart.close, daemon=True).start()
            self.destroy()


def run(config: Optional[AppConfig] = None, **kwargs) -> None:
    """Create the window and enter the Tk event loop."""
    app = RestsApp(config, **kwargs)
    app.mainloop()
