from __future__ import annotations

import logging
import signal
import subprocess
import threading
from abc import ABC, abstractmethod

import numpy as np

log = logging.getLogger("whisp.audio")


class Recorder(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> np.ndarray: ...


class SoundDeviceRecorder(Recorder):
    def __init__(self, sample_rate: int = 16000) -> None:
        import sounddevice as sd

        self._sd = sd
        self.sample_rate = sample_rate
        self._frames: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None

    def start(self) -> None:
        with self._lock:
            self._frames = []

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                log.debug("audio status: %s", status)
            with self._lock:
                self._frames.append(np.copy(indata[:, 0]))

        self._stream = self._sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=callback,
        )
        self._stream.start()

    def stop(self) -> np.ndarray:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            if not self._frames:
                return np.zeros((0,), dtype=np.float32)
            return np.concatenate(self._frames).astype(np.float32, copy=False)


class PipewireRecorder(Recorder):
    def __init__(self, sample_rate: int = 16000) -> None:
        self.sample_rate = sample_rate
        self._proc: subprocess.Popen[bytes] | None = None
        self._buf = bytearray()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._buf = bytearray()
        self._proc = subprocess.Popen(
            [
                "pw-record",
                "--rate",
                str(self.sample_rate),
                "--channels",
                "1",
                "--format",
                "f32",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert self._proc.stdout is not None
        stdout = self._proc.stdout

        def _read() -> None:
            while True:
                chunk = stdout.read(4096)
                if not chunk:
                    break
                self._buf.extend(chunk)

        self._thread = threading.Thread(target=_read, daemon=True)
        self._thread.start()

    def stop(self) -> np.ndarray:
        proc = self._proc
        self._proc = None
        if proc is None:
            return np.zeros((0,), dtype=np.float32)
        proc.send_signal(signal.SIGINT)
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not self._buf:
            return np.zeros((0,), dtype=np.float32)
        return np.frombuffer(bytes(self._buf), dtype=np.float32).copy()


def make_recorder(sample_rate: int = 16000) -> Recorder:
    try:
        rec = SoundDeviceRecorder(sample_rate)
        # Fail fast if PortAudio cannot open the default source.
        rec.start()
        rec.stop()
        device = rec._sd.default.device[0]
        try:
            name = rec._sd.query_devices(device).get("name", str(device))
        except Exception:  # noqa: BLE001
            name = str(device)
        log.info("audio backend: sounddevice [%s] %s @ %s Hz", device, name, sample_rate)
        return SoundDeviceRecorder(sample_rate)
    except Exception as exc:  # noqa: BLE001
        log.warning("sounddevice unavailable (%s); falling back to pw-record", exc)
        return PipewireRecorder(sample_rate)
