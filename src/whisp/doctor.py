from __future__ import annotations

import grp
import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path

from whisp.config import load_config


def _check_capture_source(ok, warn) -> None:  # noqa: ANN001
    try:
        out = subprocess.check_output(
            ["amixer", "-c", "0", "sget", "Internal Mic"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return
    internal_on = "[on]" in out.split("Mono:", 1)[-1]
    try:
        hs = subprocess.check_output(
            ["amixer", "-c", "0", "sget", "Headset Mic"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        headset_on = "[on]" in hs.split("Mono:", 1)[-1]
    except (OSError, subprocess.CalledProcessError):
        headset_on = False
    if headset_on and not internal_on:
        warn(
            "ALSA capture is Headset Mic, Internal Mic is off. "
            "Headphones without a mic record silence. "
            "amixer -c 0 sset 'Internal Mic' cap"
        )
    elif internal_on:
        ok("ALSA capture: Internal Mic")


def run_doctor() -> int:
    cfg = load_config(write_default=True)
    failures = 0

    def ok(msg: str) -> None:
        print(f"  ok   {msg}")

    def warn(msg: str) -> None:
        print(f"  warn {msg}")

    def fail(msg: str) -> None:
        nonlocal failures
        failures += 1
        print(f"  fail {msg}")

    print("Whisp doctor")
    print(f"config: {cfg.path}")

    print("\nSession")
    if os.environ.get("WAYLAND_DISPLAY"):
        ok(f"WAYLAND_DISPLAY={os.environ['WAYLAND_DISPLAY']}")
    else:
        warn("WAYLAND_DISPLAY is unset; paste needs a Wayland session")
    if os.environ.get("XDG_CURRENT_DESKTOP"):
        ok(f"desktop={os.environ['XDG_CURRENT_DESKTOP']}")

    print("\nInput / uinput")
    user = pwd.getpwuid(os.getuid()).pw_name
    try:
        input_group = grp.getgrnam("input")
        if user in input_group.gr_mem:
            ok(f"{user} is in group input")
        else:
            fail(
                f"{user} is not in group input — sudo usermod -aG input {user} "
                "and log out"
            )
    except KeyError:
        fail("group 'input' does not exist")

    uinput = Path("/dev/uinput")
    if not uinput.exists():
        fail("/dev/uinput missing; load the uinput kernel module")
    else:
        mode = uinput.stat()
        perms = stat.filemode(mode.st_mode)
        writable = os.access(uinput, os.W_OK)
        if writable:
            ok(f"/dev/uinput {perms} (writable)")
        else:
            fail(
                f"/dev/uinput {perms} is not writable. Copy "
                "packaging/udev/99-whisp-uinput.rules to /etc/udev/rules.d/, "
                "then: sudo udevadm control --reload-rules && sudo udevadm trigger"
            )

    try:
        from whisp.hotkey import describe_keyboards, find_keyboards, uinput_available

        keyboards = find_keyboards()
        if keyboards:
            ok(f"{len(keyboards)} keyboard(s) readable")
            for dev in keyboards:
                ok(f"  {dev.path} {dev.name}")
                dev.close()
        else:
            fail("no readable keyboards — input group membership required")
            for row in describe_keyboards():
                print(f"       {row}")
        available, detail = uinput_available()
        if available:
            ok("can create a uinput virtual keyboard")
        else:
            fail(f"cannot create uinput device: {detail}")
    except PermissionError as exc:
        fail(str(exc))
    except ImportError as exc:
        fail(f"python dependency missing: {exc}")

    print("\nClipboard")
    if shutil.which("wl-copy") and shutil.which("wl-paste"):
        ok("wl-clipboard installed")
    else:
        fail("wl-clipboard missing — sudo apt install wl-clipboard")

    print("\nAudio")
    if shutil.which("pw-record"):
        ok("pw-record present")
    else:
        warn("pw-record missing")
    _check_capture_source(ok, warn)
    try:
        import numpy as np
        import sounddevice as sd

        devices = sd.query_devices()
        default = sd.default.device
        ok(f"sounddevice default input={default[0] if default else '?'}")
        ins = [
            d["name"]
            for d in devices
            if d.get("max_input_channels", 0) > 0
        ]
        if ins:
            ok(f"input devices: {', '.join(ins[:5])}")
        else:
            fail("no input devices visible to PortAudio")
        probe = sd.rec(int(0.4 * 16000), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        samples = np.asarray(probe[:, 0], dtype=np.float32)
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        dc = float(np.mean(samples)) if samples.size else 0.0
        ac = float(np.sqrt(max(0.0, rms * rms - dc * dc)))
        if abs(dc) > 0.4 and ac < 0.05:
            fail(
                f"mic looks dead (dc={dc:.2f} ac={ac:.3f}) — headphones selected "
                "as headset mic with no microphone. In GNOME Settings → Sound → Input, "
                "pick the internal microphone"
            )
        elif peak < 0.005:
            warn(f"mic is very quiet (peak={peak:.4f})")
        else:
            ok(f"mic probe peak={peak:.3f} rms={rms:.3f} dc={dc:.3f}")
    except Exception as exc:  # noqa: BLE001
        warn(f"sounddevice: {exc} (pw-record fallback may still work)")

    print("\nOllama")
    try:
        import httpx

        tags = httpx.get(f"{cfg.ollama_host}/api/tags", timeout=2.0)
        tags.raise_for_status()
        names = [m.get("name", "") for m in tags.json().get("models", [])]
        ok(f"reachable at {cfg.ollama_host} ({len(names)} models)")
        if any(
            n == cfg.ollama_model or n.startswith(cfg.ollama_model + ":")
            for n in names
        ):
            ok(f"cleanup model {cfg.ollama_model} is pulled")
        else:
            warn(
                f"{cfg.ollama_model} is not pulled — ollama pull {cfg.ollama_model} "
                "(cleanup will fall back to raw ASR until then)"
            )
            if names:
                print(f"       installed: {', '.join(names)}")
    except Exception as exc:  # noqa: BLE001
        warn(f"Ollama not reachable: {exc} (cleanup will be skipped)")

    print("\nWhisper")
    print(f"       model {cfg.whisper_model} downloads on first daemon start")

    print("\nHost setup (once)")
    root = Path(__file__).resolve().parents[2]
    print(
        f"""\
  sudo apt install wl-clipboard
  sudo cp {root}/packaging/udev/99-whisp-uinput.rules /etc/udev/rules.d/
  sudo udevadm control --reload-rules && sudo udevadm trigger
  sudo usermod -aG input {user}
  ollama pull {cfg.ollama_model}
  # then log out and back in so the input group applies
"""
    )

    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("ready")
    return 0
