from __future__ import annotations

import os
import sys
import threading

_HIDE = "\033[?25l"
_SHOW = "\033[?25h"
_CR_CLEAR = "\r\033[2K"
_RESET = "\033[0m"


def _hotkey_label(key_name: str) -> str:
    name = key_name.removeprefix("KEY_").replace("_", " ").title()
    aliases = {"Capslock": "Caps Lock", "Rightalt": "Right Alt", "Leftalt": "Left Alt"}
    return aliases.get(name, name)


class StatusLine:
    """One-line TTY glyph for quiet mode. No-op if stderr is not a terminal."""

    def __init__(self, *, enabled: bool, hotkey: str) -> None:
        self.enabled = bool(enabled) and sys.stderr.isatty()
        self._color = self.enabled and not os.environ.get("NO_COLOR")
        self._hotkey = _hotkey_label(hotkey)
        self._lock = threading.Lock()
        self._state = "idle"
        self._pulse = False
        self._gen = 0
        self._pulse_stop = threading.Event()
        self._pulse_thread: threading.Thread | None = None
        self._pulse_id = 0
        self._drawn = False
        if self.enabled:
            self._write(_HIDE)
            self.set("idle")

    def set(self, state: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._state = state
            self._pulse = False
            if state == "recording":
                self._start_pulse_locked()
            else:
                self._stop_pulse_locked()
            self._draw_locked()
        if state in {"done", "cancel", "error"}:
            timer = threading.Timer(1.0, self._revert_idle, args=(gen,))
            timer.daemon = True
            timer.start()

    def close(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._stop_pulse_locked()
            if self._drawn:
                self._write(_CR_CLEAR + _SHOW + "\n")
            else:
                self._write(_SHOW)
            self._drawn = False
        self.enabled = False

    def _revert_idle(self, gen: int) -> None:
        with self._lock:
            if not self.enabled or gen != self._gen:
                return
            if self._state not in {"done", "cancel", "error"}:
                return
            self._state = "idle"
            self._draw_locked()

    def _start_pulse_locked(self) -> None:
        self._pulse_stop.clear()
        self._pulse_id += 1
        pid = self._pulse_id
        self._pulse_thread = threading.Thread(
            target=self._pulse_loop, args=(pid,), daemon=True, name="whisp-status"
        )
        self._pulse_thread.start()

    def _stop_pulse_locked(self) -> None:
        self._pulse_stop.set()
        self._pulse_id += 1
        self._pulse_thread = None

    def _pulse_loop(self, pid: int) -> None:
        while not self._pulse_stop.wait(0.4):
            with self._lock:
                if not self.enabled or pid != self._pulse_id or self._state != "recording":
                    return
                self._pulse = not self._pulse
                self._draw_locked()

    def _draw_locked(self) -> None:
        glyph, label, paint = self._frame()
        if self._color:
            line = f"{paint}{glyph}  {label}{_RESET}"
        else:
            line = f"{glyph}  {label}"
        self._write(_CR_CLEAR + line)
        self._drawn = True

    def _frame(self) -> tuple[str, str, str]:
        state = self._state
        if state == "recording":
            paint = self._sgr("1;91") if self._pulse else self._sgr("31")
            return "◉", "recording", paint
        if state == "processing":
            return "◐", "…", self._sgr("33")
        if state == "done":
            return "✓", "pasted", self._sgr("32")
        if state == "cancel":
            return "–", "ignored", self._sgr("2")
        if state == "error":
            return "✕", "error", self._sgr("31")
        return "○", f"whisp   hold {self._hotkey}", self._sgr("2")

    def _sgr(self, code: str) -> str:
        if not self._color:
            return ""
        return f"\033[{code}m"

    @staticmethod
    def _write(text: str) -> None:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except OSError:
            pass
