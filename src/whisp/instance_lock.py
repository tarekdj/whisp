from __future__ import annotations

import fcntl
import os
from pathlib import Path

LOCK_FILE_NAME = "whisp.lock"


class InstanceAlreadyRunning(Exception):
    def __init__(self, pid: int | None) -> None:
        self.pid = pid


def instance_lock_path(config_path: Path) -> Path:
    return config_path.parent / LOCK_FILE_NAME


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip().split()[0]
        return int(raw)
    except (OSError, ValueError, IndexError):
        return None


def acquire_instance_lock(lock_path: Path) -> int:
    """Exclusive flock on lock_path. Return fd; keep open until process exit."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        pid = _read_lock_pid(lock_path)
        os.close(fd)
        raise InstanceAlreadyRunning(pid) from None
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd
