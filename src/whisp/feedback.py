from __future__ import annotations

import logging
import os
import shutil
import subprocess

log = logging.getLogger("whisp.feedback")

_SOUNDS = {
    "start": "/usr/share/sounds/freedesktop/stereo/message-new-instant.oga",
    "done": "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "cancel": "/usr/share/sounds/freedesktop/stereo/audio-volume-change.oga",
    "error": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
}


def beep(kind: str, enabled: bool = True) -> None:
    if not enabled:
        return
    path = _SOUNDS.get(kind)
    if not path or not os.path.exists(path):
        return
    for binary in ("canberra-gtk-play", "paplay"):
        if shutil.which(binary) is None:
            continue
        cmd = [binary, "-f", path] if binary == "canberra-gtk-play" else [binary, path]
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError as exc:
            log.debug("beep failed: %s", exc)
