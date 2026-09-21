from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time

from evdev import UInput, ecodes

log = logging.getLogger("whisp.inject")

_UNSET = object()

VTE_NAMES = (
    "gnome-terminal-server",
    "gnome-terminal",
    "org.gnome.terminal",
    "ptyxis",
    "org.gnome.ptyxis",
    "tilix",
    "com.gexperts.tilix",
    "kgx",
    "org.gnome.console",
    "xfce4-terminal",
    "alacritty",
    "kitty",
    "wezterm",
    "ghostty",
    "foot",
    "konsole",
    "terminator",
)


class Injector:
    def __init__(self, mode: str, paste: str = "auto") -> None:
        self.mode = mode
        self.paste = paste
        self._ui: UInput | None = None
        if mode == "paste":
            if shutil.which("wl-copy") is None:
                raise RuntimeError(
                    "wl-copy not found. Install wl-clipboard: sudo apt install wl-clipboard"
                )
            self._ui = UInput(
                {
                    ecodes.EV_KEY: [
                        ecodes.KEY_LEFTSHIFT,
                        ecodes.KEY_LEFTCTRL,
                        ecodes.KEY_INSERT,
                        ecodes.KEY_V,
                    ]
                },
                name="whisp-inject",
            )
        self._restore_id = 0
        self._saved_clip: bytes | None | object = _UNSET
        self._saved_primary: bytes | None | object = _UNSET

    def close(self) -> None:
        if self._ui is not None:
            self._ui.close()
            self._ui = None

    def snapshot_clipboard(self) -> None:
        """Capture the clipboard once; the next restore=True inject restores it.

        Used by streaming sessions so batch pastes (restore=False) don't
        permanently clobber the user's pre-dictation clipboard.
        """
        if self.mode != "paste" or shutil.which("wl-copy") is None:
            return
        self._saved_clip = _wl_paste(primary=False)
        self._saved_primary = _wl_paste(primary=True)

    def restore_snapshot(self) -> None:
        """Schedule a restore of the snapshot taken by snapshot_clipboard()."""
        if self._saved_clip is _UNSET:
            return
        clip = self._saved_clip
        primary = self._saved_primary
        self._saved_clip = self._saved_primary = _UNSET
        self._restore_id += 1
        self._restore_later(clip, primary, self._restore_id)  # type: ignore[arg-type]

    def discard_snapshot(self) -> None:
        """Drop a snapshot without restoring (nothing was pasted)."""
        self._saved_clip = self._saved_primary = _UNSET

    def inject(
        self,
        text: str,
        *,
        vte: bool | None = None,
        restore: bool = True,
    ) -> None:
        if not text:
            return
        if self.mode == "stdout":
            print(text, end="", flush=True)
            if restore:
                print(flush=True)
            return
        if shutil.which("wl-copy") is None:
            raise RuntimeError(
                "wl-copy not found. Install wl-clipboard: sudo apt install wl-clipboard"
            )
        previous_clip: bytes | None = None
        previous_primary: bytes | None = None
        restore_id = 0
        if restore:
            self._restore_id += 1
            restore_id = self._restore_id
            if self._saved_clip is not _UNSET:
                # Streaming session: restore the pre-session clipboard, not the
                # last batch chunk that batch pastes left behind.
                previous_clip = self._saved_clip  # type: ignore[assignment]
                previous_primary = self._saved_primary  # type: ignore[assignment]
                self._saved_clip = self._saved_primary = _UNSET
            else:
                previous_clip = _wl_paste(primary=False)
                previous_primary = _wl_paste(primary=True)
        _wl_copy(text, primary=False)
        if restore:
            _wl_copy(text, primary=True)
        if self.mode == "clipboard":
            log.info("copied %s chars", len(text))
            return
        chord = self._chord(vte=vte)
        time.sleep(0.05)
        _tap(self._ui, *chord)
        log.info("pasted %s chars with %s", len(text), _chord_name(chord))
        if restore:
            self._restore_later(previous_clip, previous_primary, restore_id)

    def _chord(self, vte: bool | None = None) -> tuple[int, ...]:
        if self.paste == "ctrl+shift+v":
            return (ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_V)
        if self.paste == "shift+insert":
            return (ecodes.KEY_LEFTSHIFT, ecodes.KEY_INSERT)
        if vte is None:
            vte = focused_is_vte()
        if vte:
            return (ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_V)
        return (ecodes.KEY_LEFTSHIFT, ecodes.KEY_INSERT)

    def _restore_later(
        self, clip: bytes | None, primary: bytes | None, restore_id: int, delay: float = 0.4
    ) -> None:
        def _restore() -> None:
            time.sleep(delay)
            if restore_id != self._restore_id:
                return
            if clip is not None:
                _wl_copy_bytes(clip, primary=False)
            if primary is not None:
                _wl_copy_bytes(primary, primary=True)

        threading.Thread(target=_restore, daemon=True).start()


def focused_app_name() -> str | None:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return None
    try:
        Atspi.init()
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            for j in range(min(app.get_child_count(), 32)):
                win = app.get_child_at_index(j)
                if win is None:
                    continue
                states = win.get_state_set()
                if states.contains(Atspi.StateType.ACTIVE):
                    return (app.get_name() or "").lower()
    except Exception as exc:  # noqa: BLE001
        log.debug("AT-SPI focus probe failed: %s", exc)
    return None


def focused_is_vte(name: str | None = None) -> bool:
    if name is None:
        name = focused_app_name()
    if not name:
        return False
    return any(token in name for token in VTE_NAMES)


def _wl_copy(text: str, *, primary: bool) -> None:
    cmd = ["wl-copy"]
    if primary:
        cmd.append("--primary")
    subprocess.run(cmd, input=text.encode("utf-8"), check=True)


def _wl_paste(*, primary: bool) -> bytes | None:
    cmd = ["wl-paste", "-n"]
    if primary:
        cmd.append("--primary")
        # wl-paste --primary with no selection exits 1.
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def _wl_copy_bytes(data: bytes, *, primary: bool) -> None:
    cmd = ["wl-copy"]
    if primary:
        cmd.append("--primary")
    subprocess.run(cmd, input=data, check=False)


def _tap(ui: UInput | None, *codes: int) -> None:
    if ui is None:
        raise RuntimeError("uinput device is not open")
    for code in codes:
        ui.write(ecodes.EV_KEY, code, 1)
    ui.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)
    time.sleep(0.02)
    for code in reversed(codes):
        ui.write(ecodes.EV_KEY, code, 0)
    ui.write(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)


def _chord_name(codes: tuple[int, ...]) -> str:
    if codes == (ecodes.KEY_LEFTCTRL, ecodes.KEY_LEFTSHIFT, ecodes.KEY_V):
        return "ctrl+shift+v"
    return "shift+insert"
