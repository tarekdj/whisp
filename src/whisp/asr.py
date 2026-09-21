from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger("whisp.asr")

# Hold-to-talk clips are 1–3s. faster-whisper's default VAD uses
# min_silence_duration_ms=2000, which swallows the whole utterance.
_VAD_SHORT = {
    "threshold": 0.35,
    "min_speech_duration_ms": 100,
    "min_silence_duration_ms": 100,
    "speech_pad_ms": 200,
}


class Transcriber:
    def __init__(self, model_name: str, sample_rate: int = 16000) -> None:
        from faster_whisper import WhisperModel

        threads = max(1, min(4, (os.cpu_count() or 2) // 2 or 1))
        log.info("loading Whisper model %s (cpu int8, %s threads)", model_name, threads)
        self.sample_rate = sample_rate
        self.model = WhisperModel(
            model_name,
            device="cpu",
            compute_type="int8",
            cpu_threads=threads,
        )

    def transcribe(self, audio: np.ndarray, language: str = "") -> str:
        if audio.size == 0:
            return ""
        audio = _prepare_audio(audio)
        peak, rms, dc = _stats(audio)
        log.info(
            "audio %.2fs peak=%.3f rms=%.3f dc=%.3f",
            audio.size / self.sample_rate,
            peak,
            rms,
            dc,
        )
        if peak < 0.005:
            log.info("audio too quiet (peak %.4f); skipping", peak)
            return ""

        text = self._run(audio, language=language, vad=True)
        if not text:
            log.info("empty after VAD; retrying without VAD")
            text = self._run(audio, language=language, vad=False)
        return text

    def _run(self, audio: np.ndarray, *, language: str, vad: bool) -> str:
        segments, info = self.model.transcribe(
            audio,
            language=language or None,
            vad_filter=vad,
            vad_parameters=_VAD_SHORT if vad else None,
            beam_size=1,
            without_timestamps=True,
        )
        text = "".join(segment.text for segment in segments).strip()
        detected = getattr(info, "language", None)
        log.info(
            "asr %s vad=%s: %s",
            detected or language or "auto",
            vad,
            text[:120] or "(empty)",
        )
        return text


def _prepare_audio(audio: np.ndarray) -> np.ndarray:
    audio = np.ascontiguousarray(audio, dtype=np.float32)
    peak, rms, dc = _stats(audio)
    if abs(dc) > 0.2 and rms > 1e-6 and abs(dc) / rms > 0.85:
        log.warning(
            "capture is DC-dominated (dc=%.2f rms=%.2f) — headset jack with no mic? "
            "Switch the input to the internal microphone",
            dc,
            rms,
        )
    if abs(dc) >= 0.01:
        audio = audio - np.float32(dc)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / np.float32(peak)
    return audio


def _stats(audio: np.ndarray) -> tuple[float, float, float]:
    if audio.size == 0:
        return 0.0, 0.0, 0.0
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio))))
    dc = float(np.mean(audio))
    return peak, rms, dc
