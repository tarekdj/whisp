from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from whisp.asr import Transcriber
from whisp.audio import Recorder
from whisp.cleanup import cleanup_text

log = logging.getLogger("whisp.stream")

TENTATIVE_WORDS = 2
MIN_TICK_S = 0.5
_PUNCT_START = ",.;:!?…)]}»"
_STRIP = ".,;:!?…«»\"'()[]{}"


def update_stable(
    frozen: str, text: str, *, tentative_words: int = TENTATIVE_WORDS
) -> tuple[str, str]:
    """Grow a frozen word prefix. Last `tentative_words` stay uncommitted.

    Whisper may return a full re-transcript, a case-tweaked prefix, an overlap
    from a trailing window, or only the new tail (especially with initial_prompt).
    """
    asr = text.split()
    frozen_w = frozen.split()
    if not asr:
        return frozen, ""
    aligned = _align_words(frozen_w, asr, tail=True)
    if len(aligned) > tentative_words:
        proposed = aligned[:-tentative_words]
        if len(proposed) >= len(frozen_w):
            frozen_w = proposed
    tentative = aligned[len(frozen_w) :]
    return " ".join(frozen_w), " ".join(tentative)


def word_delta(committed: str, newer: str, *, tail: bool = True) -> str:
    """Words in `newer` after the already-committed prefix."""
    c, n = committed.split(), newer.split()
    if not n:
        return ""
    aligned = _align_words(c, n, tail=tail)
    if len(aligned) <= len(c):
        return ""
    if not _is_word_prefix(c, aligned):
        return ""
    return " ".join(aligned[len(c) :])


def leftover_text(committed: str, final: str) -> str:
    """Tail to paste on release. Never re-paste the committed prefix."""
    committed = committed.strip()
    final = final.strip()
    if not committed:
        return final
    if not final:
        return ""
    delta = word_delta(committed, final, tail=False)
    if delta:
        return delta
    fw, tw = committed.split(), final.split()
    i = 0
    while i < len(fw) and i < len(tw) and _norm_word(fw[i]) == _norm_word(tw[i]):
        i += 1
    if i:
        return " ".join(tw[i:])
    # Committed words appear later in the final pass (revision of the opening).
    idx = _find_subseq(fw, tw)
    if idx is not None:
        return " ".join(tw[idx + len(fw) :])
    return ""


def spaced_chunk(pasted_any: bool, chunk: str) -> str:
    chunk = chunk.strip()
    if not chunk:
        return ""
    if pasted_any and chunk[0] not in _PUNCT_START:
        return " " + chunk
    return chunk


def _norm_word(word: str) -> str:
    return word.casefold().strip(_STRIP)


def _norm_seq(words: list[str]) -> list[str]:
    return [_norm_word(w) for w in words]


def _is_word_prefix(prefix: list[str], words: list[str]) -> bool:
    if len(prefix) > len(words):
        return False
    return _norm_seq(prefix) == _norm_seq(words[: len(prefix)])


def _find_subseq(needle: list[str], hay: list[str]) -> int | None:
    if not needle or len(needle) > len(hay):
        return None
    n, h = _norm_seq(needle), _norm_seq(hay)
    last = len(h) - len(n)
    for i in range(last + 1):
        if h[i : i + len(n)] == n:
            return i
    return None


def _align_words(
    frozen_w: list[str], asr: list[str], *, tail: bool = True
) -> list[str]:
    """Monotonic word list: keep frozen as-is, append newly heard words from ASR."""
    if not frozen_w:
        return asr
    if _is_word_prefix(frozen_w, asr):
        return frozen_w + asr[len(frozen_w) :]
    idx = _find_subseq(frozen_w, asr)
    if idx is not None:
        return frozen_w + asr[idx + len(frozen_w) :]
    max_k = min(len(frozen_w), len(asr))
    for k in range(max_k, 0, -1):
        if _norm_seq(frozen_w[-k:]) == _norm_seq(asr[:k]):
            return frozen_w + asr[k:]
    if tail:
        # Whisper omitted the frozen prefix (prompt / window).
        return frozen_w + asr
    return frozen_w


class StreamSession:
    """While holding: every interval, freeze new words, cleanup, paste that batch."""

    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        injector: object,
        *,
        sample_rate: int,
        language: str,
        interval_s: float,
        cleanup: bool,
        ollama_host: str,
        ollama_model: str,
        cleanup_timeout_s: float,
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._injector = injector
        self._sample_rate = sample_rate
        self._language = language
        self._interval_s = max(0.4, float(interval_s))
        self._cleanup = cleanup
        self._ollama_host = ollama_host
        self._ollama_model = ollama_model
        self._cleanup_timeout_s = cleanup_timeout_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.vte = False
        self.committed = ""
        self.pasted_any = False

    def start(self, *, vte: bool) -> None:
        self.stop()
        self.vte = vte
        self.committed = ""
        self.pasted_any = False
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="whisp-stream"
        )
        self._thread.start()

    def stop(self) -> str:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=45)
        return self.committed

    def _loop(self) -> None:
        if self._stop.wait(MIN_TICK_S):
            return
        while not self._stop.is_set():
            started = time.monotonic()
            self._tick()
            wait = self._interval_s - (time.monotonic() - started)
            if wait <= 0:
                continue
            if self._stop.wait(wait):
                return

    def _tick(self) -> None:
        try:
            audio = self._recorder.snapshot()
            if audio.size < int(MIN_TICK_S * self._sample_rate):
                return
            # Full buffer, no initial_prompt: prompting with already-pasted text
            # makes Whisper omit those words, so freeze/delta would skip until release.
            text = self._transcriber.transcribe(
                audio,
                language=self._language,
                partial=True,
            )
            if not text:
                return
            frozen, _tentative = update_stable(self.committed, text)
            delta = word_delta(self.committed, frozen)
            if not delta:
                log.debug("stream tick: no new frozen words (%s)", frozen[:80] or "(none)")
                return
            if self._stop.is_set():
                return
            chunk = delta
            if self._cleanup:
                chunk = cleanup_text(
                    delta,
                    host=self._ollama_host,
                    model=self._ollama_model,
                    timeout=self._cleanup_timeout_s,
                )
            if self._stop.is_set():
                return
            paste = spaced_chunk(self.pasted_any, chunk)
            if not paste:
                return
            inject: Callable[..., None] | None = getattr(self._injector, "inject", None)
            if inject is None:
                return
            inject(paste, vte=self.vte, restore=False)
            self.committed = frozen
            self.pasted_any = True
            log.info("batch paste %r", paste[:80])
        except Exception:
            log.exception("stream tick failed")
