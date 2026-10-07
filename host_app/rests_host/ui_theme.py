"""Presentation helpers for the Tkinter UI: palette, fonts and widgets.

View code only - no model, UART, language or protocol knowledge lives
here, so ``app.py`` can stay about behaviour. The palette is a calm
light-grey window with white cards, dark slate text and a single steel-blue
accent; the two caption panels are deliberately the easiest things on the
screen to read (larger fonts, generous padding, wrapped and scrollable).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Tuple

#: Everything is derived from this dictionary so the app stays consistent.
PALETTE = {
    "window": "#EDF0F3",
    "card": "#FFFFFF",
    "border": "#D3DAE1",
    "text": "#16212B",
    "muted": "#5A6874",
    "accent": "#1F5F8B",
    "accent_dark": "#164A6B",
    "accent_soft": "#EAF2F9",
    "ok": "#1E7A44",
    "ok_soft": "#E6F4EB",
    "warn": "#8A5300",
    "warn_soft": "#FCF2E3",
    "error": "#B3261E",
    "error_soft": "#FBEAE8",
    "source_bg": "#FFFFFF",
    "source_fg": "#16212B",
    "target_bg": "#EDF4FC",
    "target_fg": "#0D1B29",
    "target_border": "#5E93C4",
}

FONTS = {
    "section": ("Segoe UI", 11, "bold"),
    "label": ("Segoe UI", 10, "bold"),
    "body": ("Segoe UI", 10),
    "small": ("Segoe UI", 9),
    "status": ("Segoe UI", 10),
    "pill": ("Segoe UI", 9, "bold"),
    "caption": ("Segoe UI", 13),
    "caption_big": ("Segoe UI", 15),
    "caption_hint": ("Segoe UI", 13, "italic"),
    "ptt": ("Segoe UI", 14, "bold"),
}

#: Status-line / indicator colours per severity level.
SEVERITY_COLORS = {
    "info": PALETTE["muted"],
    "busy": PALETTE["accent"],
    "ok": PALETTE["ok"],
    "warn": PALETTE["warn"],
    "error": PALETTE["error"],
}

_ERROR_WORDS = (
    "error",
    "failed",
    "failure",
    "cannot",
    "invalid",
    "unavailable",
    "not available",
    "exception",
    "refused",
)
_WARN_WORDS = (
    "not sent",
    "ignored",
    "no windows voice",
    "no windows locale",
    "missing",
    "warning",
    "weaker",
    "rejected",
)
_BUSY_WORDS = ("processing", "recording", "connecting", "handshaking", "loading")
_OK_WORDS = ("ready", "handshake ok", "sent ", "translated", "queued", " ok", ": ok")


def classify_status(message: str) -> str:
    """Map a status message to ``error`` / ``warn`` / ``busy`` / ``ok`` / ``info``."""
    lower = message.lower()
    if any(word in lower for word in _ERROR_WORDS):
        return "error"
    if any(word in lower for word in _WARN_WORDS):
        return "warn"
    if any(word in lower for word in _BUSY_WORDS):
        return "busy"
    if any(lower.startswith(word) or word in lower for word in _OK_WORDS):
        return "ok"
    return "info"


def shorten(text: str, limit: int = 160) -> str:
    """One-line version of a message for the status bar."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "\u2026"


def apply_theme(root: tk.Tk) -> None:
    """Apply the palette, fonts and ttk style tweaks to the whole window."""
    root.configure(bg=PALETTE["window"])
    style = ttk.Style(root)
    style.configure(".", font=FONTS["body"])
    style.configure("TButton", font=FONTS["body"], padding=(12, 5))
    style.configure("TEntry", font=FONTS["body"], padding=(6, 5))
    style.configure("TCombobox", font=FONTS["body"], padding=(6, 5))
    style.configure(
        "TCheckbutton",
        font=FONTS["body"],
        background=PALETTE["card"],
        foreground=PALETTE["text"],
    )
    style.map(
        "TCheckbutton",
        background=[("active", PALETTE["card"]), ("selected", PALETTE["card"])],
        foreground=[("active", PALETTE["text"]), ("disabled", PALETTE["muted"])],
    )


def make_card(parent: tk.Misc, title: str, *, accent: bool = False) -> Tuple[tk.Frame, tk.Frame]:
    """Create a section title above a white content card.

    Returns ``(shell, body)``: ``shell`` is the grid/pack target for the
    caller, ``body`` is an *empty* frame (no children yet) where the
    caller may freely ``grid`` its widgets.
    """
    shell = tk.Frame(parent, bg=PALETTE["window"])
    header = tk.Label(
        shell,
        text=title,
        font=FONTS["section"],
        fg=PALETTE["accent"] if accent else PALETTE["text"],
        bg=PALETTE["window"],
        anchor="w",
    )
    header.pack(fill="x", pady=(0, 5))
    body = tk.Frame(
        shell,
        bg=PALETTE["card"],
        highlightbackground=PALETTE["accent"] if accent else PALETTE["border"],
        highlightthickness=2 if accent else 1,
        highlightcolor=PALETTE["accent"] if accent else PALETTE["border"],
        padx=14,
        pady=8,
    )
    body.pack(fill="both", expand=True)
    return shell, body


def caption_panel(
    parent: tk.Misc,
    title: str,
    *,
    background: str,
    foreground: str,
    font,
    placeholder: str,
    accent: bool = False,
) -> Tuple[tk.Frame, tk.Text]:
    """A titled card holding a read-only, wrapped, scrollable text panel.

    The caller must ``grid``-manage the returned shell.  The text widget is
    given a deliberately small requested height (a ``tk.Text`` otherwise
    asks for ~24 lines) so that the surrounding grid can share space by
    weight instead of letting one panel's request starve its neighbour.
    """
    shell, body = make_card(parent, title, accent=accent)
    holder = tk.Frame(body, bg=background, highlightthickness=0)
    holder.pack(fill="both", expand=True)
    text = tk.Text(
        holder,
        wrap="word",
        state="disabled",
        # One requested line: the panel is filled by `fill`/`expand`, and a
        # large request would make `pack` squeeze the row instead of filling
        # it (the first caption line would be clipped).
        height=1,
        bg=background,
        fg=foreground,
        font=font,
        relief="flat",
        bd=0,
        padx=14,
        pady=8,
        spacing1=2,
        spacing3=6,
        highlightthickness=0,
        cursor="arrow",
    )
    bar = ttk.Scrollbar(holder, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text._rests_placeholder = placeholder  # noqa: SLF001 - tk widget attribute
    text.tag_configure(
        "hint", foreground=PALETTE["muted"], font=FONTS["caption_hint"]
    )
    text.tag_configure("body", foreground=foreground)
    text.tag_configure("error", foreground=PALETTE["error"], font=FONTS["caption"])

    def _wheel(event):
        delta = getattr(event, "delta", 0)
        if delta:
            text.yview_scroll(-1 * (delta // 120 or (1 if delta > 0 else -1)), "units")
        return "break"

    text.bind("<MouseWheel>", _wheel)
    return shell, text
