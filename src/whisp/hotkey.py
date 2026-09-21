from __future__ import annotations

import logging
import select
from collections.abc import Callable

from evdev import InputDevice, UInput, ecodes, list_devices

log = logging.getLogger("whisp.hotkey")

_ALIASES = {
    "CAPSLOCK": "KEY_CAPSLOCK",
    "CAPS_LOCK": "KEY_CAPSLOCK",
    "CAPS": "KEY_CAPSLOCK",
    "RIGHTALT": "KEY_RIGHTALT",
    "ALT_R": "KEY_RIGHTALT",
    "COMPOSE": "KEY_COMPOSE",
}


def resolve_key(name: str) -> int:
    key = name.strip().upper().replace("-", "_").replace(" ", "_")
    key = _ALIASES.get(key, key)
    if not key.startswith("KEY_"):
        key = "KEY_" + key
    try:
        return int(getattr(ecodes, key))
    except AttributeError as exc:
        raise ValueError(f"Unknown hotkey {name!r} (tried {key})") from exc


def _is_keyboard(dev: InputDevice) -> bool:
    keys = dev.capabilities().get(ecodes.EV_KEY, [])
    return ecodes.KEY_A in keys and ecodes.KEY_Z in keys


def find_keyboards() -> list[InputDevice]:
    devices: list[InputDevice] = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
        except (PermissionError, OSError) as exc:
            log.debug("skip %s: %s", path, exc)
            continue
        name = (dev.name or "").lower()
        if "whisp" in name:
            dev.close()
            continue
        if not _is_keyboard(dev):
            dev.close()
            continue
        devices.append(dev)
    return devices


class HotkeyListener:
    """Watch KEY_DOWN / KEY_UP on every real keyboard.

    When grab=True, the physical devices are grabbed and every event except
    the hotkey is replayed through a virtual keyboard so Caps Lock does not
    toggle. Grab is never taken unless that forwarder opened successfully.
    """

    def __init__(self, key_name: str, *, grab: bool = True) -> None:
        self.code = resolve_key(key_name)
        self.grab = grab
        self._devices: list[InputDevice] = []
        self._forwarders: dict[int, UInput] = {}
        self._running = False

    def start(self) -> None:
        self._devices = find_keyboards()
        if not self._devices:
            raise PermissionError(
                "No keyboard devices readable. Add your user to the input group "
                "(sudo usermod -aG input $USER) and log out."
            )
        if self.grab:
            for i, dev in enumerate(list(self._devices)):
                try:
                    ui = UInput.from_device(dev, name=f"whisp-fwd-{i}")
                    dev.grab()
                    self._forwarders[dev.fd] = ui
                    log.info("grabbed %s (%s)", dev.path, dev.name)
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "cannot grab %s (%s); Caps Lock will still toggle on this device",
                        dev.path,
                        exc,
                    )
        else:
            for dev in self._devices:
                log.info("listening on %s (%s) without grab", dev.path, dev.name)

    def close(self) -> None:
        self._running = False
        for dev in self._devices:
            try:
                if dev.fd in self._forwarders:
                    dev.ungrab()
            except OSError:
                pass
            try:
                dev.close()
            except OSError:
                pass
        self._devices.clear()
        for ui in self._forwarders.values():
            try:
                ui.close()
            except OSError:
                pass
        self._forwarders.clear()

    def stop(self) -> None:
        self._running = False

    def run(self, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        if not self._devices:
            self.start()
        by_fd = {dev.fd: dev for dev in self._devices}
        self._running = True
        log.info("waiting for hotkey (code %s)", self.code)
        while self._running:
            if not by_fd:
                log.error("all keyboard devices gone")
                break
            ready, _, _ = select.select(list(by_fd), [], [], 0.25)
            for fd in ready:
                dev = by_fd[fd]
                try:
                    events = list(dev.read())
                except OSError as exc:
                    log.warning("lost %s: %s", dev.path, exc)
                    by_fd.pop(fd, None)
                    continue
                for event in events:
                    is_hotkey = event.type == ecodes.EV_KEY and event.code == self.code
                    forwarder = self._forwarders.get(fd)
                    if forwarder is not None and not is_hotkey:
                        forwarder.write_event(event)
                    if not is_hotkey:
                        continue
                    if event.value == 1:
                        on_press()
                    elif event.value == 0:
                        on_release()


def describe_keyboards() -> list[str]:
    rows: list[str] = []
    try:
        paths = list_devices()
    except PermissionError as exc:
        return [f"cannot list /dev/input: {exc}"]
    except OSError as exc:
        return [f"cannot list /dev/input: {exc}"]
    for path in paths:
        try:
            dev = InputDevice(path)
        except (PermissionError, OSError) as exc:
            rows.append(f"{path}: unreadable ({exc})")
            continue
        try:
            kind = "keyboard" if _is_keyboard(dev) else "other"
            rows.append(f"{path}: {dev.name} [{kind}]")
        finally:
            dev.close()
    return rows


def uinput_available() -> tuple[bool, str]:
    try:
        ui = UInput({ecodes.EV_KEY: [ecodes.KEY_INSERT]}, name="whisp-probe")
        ui.close()
        return True, "writable"
    except OSError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 — probe must not crash doctor
        return False, str(exc)
