from __future__ import annotations

import logging
import re
import shutil
import signal
import subprocess
import threading
from abc import ABC, abstractmethod

import numpy as np

log = logging.getLogger("whisp.audio")

# pw-record understands this PipeWire alias; tracks GNOME's default input without pactl.
_PW_DEFAULT_TARGET = "@DEFAULT_AUDIO_SOURCE@"
_WPCTL_SOURCE_RE = re.compile(r"\*\s+(\d+)\.\s+(.+?)\s+\[")


def default_sounddevice_input(sd: object) -> tuple[int | None, str]:
    """Current PortAudio default capture device (re-query on each hold)."""
    default = getattr(sd, "default", None)
    idx = default.device[0] if default and default.device else None
    if idx is None:
        return None, "unknown"
    try:
        name = sd.query_devices(idx).get("name", str(idx))  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        name = str(idx)
    return int(idx), f"[{idx}] {name}"


def _wpctl_default_source_label() -> str | None:
    if not shutil.which("wpctl"):
        return None
    try:
        out = subprocess.check_output(
            ["wpctl", "status"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    in_sources = False
    for line in out.splitlines():
        if "Sources:" in line:
            in_sources = True
            continue
        if not in_sources:
            continue
        if "Source endpoints:" in line or "Streams:" in line:
            break
        match = _WPCTL_SOURCE_RE.search(line)
        if match:
            return f"[{match.group(1)}] {match.group(2).strip()}"
    return None


def pipewire_default_capture() -> tuple[str | None, str]:
    """pw-record --target and a human label for logs."""
    if shutil.which("pactl"):
        try:
            out = subprocess.check_output(
                ["pactl", "get-default-source"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=2,
            ).strip()
            if out:
                return out, out
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    label = _wpctl_default_source_label()
    if label:
        return _PW_DEFAULT_TARGET, label
    if shutil.which("pw-record"):
        return _PW_DEFAULT_TARGET, _PW_DEFAULT_TARGET
    return None, "PipeWire default (auto)"


class Recorder(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def snapshot(self) -> np.ndarray: ...

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

        device, label = default_sounddevice_input(self._sd)
        log.info("input %s @ %s Hz", label, self.sample_rate)

        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                log.debug("audio status: %s", status)
            with self._lock:
                self._frames.append(np.copy(indata[:, 0]))

        stream_kwargs: dict = {
            "samplerate": self.sample_rate,
            "channels": 1,
            "dtype": "float32",
            "callback": callback,
        }
        if device is not None:
            stream_kwargs["device"] = device
        self._stream = self._sd.InputStream(**stream_kwargs)
        self._stream.start()

    def snapshot(self) -> np.ndarray:
        with self._lock:
            if not self._frames:
                return np.zeros((0,), dtype=np.float32)
            return np.concatenate(self._frames).astype(np.float32, copy=False)

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
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            self._buf = bytearray()
        target, label = pipewire_default_capture()
        log.info("input %s @ %s Hz", label, self.sample_rate)
        cmd = [
            "pw-record",
            "--rate",
            str(self.sample_rate),
            "--channels",
            "1",
            "--format",
            "f32",
        ]
        if target:
            cmd.extend(["--target", target])
        cmd.append("-")
        self._proc = subprocess.Popen(
            cmd,
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
                with self._lock:
                    self._buf.extend(chunk)

        self._thread = threading.Thread(target=_read, daemon=True)
        self._thread.start()

    def snapshot(self) -> np.ndarray:
        with self._lock:
            raw = bytes(self._buf)
        return _f32_pcm(raw)

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
        with self._lock:
            raw = bytes(self._buf)
            self._buf = bytearray()
        return _f32_pcm(raw)


def _f32_pcm(raw: bytes) -> np.ndarray:
    n = len(raw) // 4 * 4
    if n == 0:
        return np.zeros((0,), dtype=np.float32)
    return np.frombuffer(raw[:n], dtype=np.float32).copy()


def _peak(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.max(np.abs(audio)))


def _pipewire_capture_ready() -> bool:
    return shutil.which("pw-record") is not None and pipewire_default_capture()[0] is not None


def _probe_pipewire(sample_rate: int) -> None:
    rec = PipewireRecorder(sample_rate)
    rec.start()
    rec.stop()


def _probe_sounddevice(sample_rate: int) -> float:
    rec = SoundDeviceRecorder(sample_rate)
    rec.start()
    audio = rec.stop()
    return _peak(audio)


def make_recorder(sample_rate: int = 16000) -> Recorder:
    # GNOME/PipeWire default input follows pactl, not always PortAudio's "default".
    if _pipewire_capture_ready():
        try:
            _probe_pipewire(sample_rate)
            log.info(
                "audio backend: pw-record @ %s Hz (PipeWire default source each hold)",
                sample_rate,
            )
            return PipewireRecorder(sample_rate)
        except Exception as exc:  # noqa: BLE001
            log.warning("pw-record unavailable (%s); trying sounddevice", exc)

    try:
        peak = _probe_sounddevice(sample_rate)
        if peak < 0.005 and shutil.which("pw-record") is not None:
            log.warning(
                "sounddevice default is silent (peak %.4f); using pw-record",
                peak,
            )
            _probe_pipewire(sample_rate)
            log.info(
                "audio backend: pw-record @ %s Hz (default source each hold)",
                sample_rate,
            )
            return PipewireRecorder(sample_rate)
        log.info(
            "audio backend: sounddevice @ %s Hz (default input re-resolved each hold)",
            sample_rate,
        )
        return SoundDeviceRecorder(sample_rate)
    except Exception as exc:  # noqa: BLE001
        log.warning("sounddevice unavailable (%s); falling back to pw-record", exc)
        _probe_pipewire(sample_rate)
        log.info(
            "audio backend: pw-record @ %s Hz (default source each hold)",
            sample_rate,
        )
        return PipewireRecorder(sample_rate)
