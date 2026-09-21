from __future__ import annotations

import logging

import httpx

log = logging.getLogger("whisp.cleanup")

PROMPT = """\
Fix punctuation and casing in the speech-to-text transcript.
Remove filler words (um, uh, er, euh, ben, bah) only when they are fillers.
Do not rephrase, translate, summarize, or add words.
Keep the speaker's language (French, English, or mixed).
Reply with the cleaned transcript only — no quotes, no labels.

Transcript:
{text}
"""


def cleanup_text(
    text: str,
    *,
    host: str,
    model: str,
    timeout: float,
) -> str:
    if not text.strip():
        return text
    try:
        response = httpx.post(
            f"{host}/api/generate",
            json={
                "model": model,
                "prompt": PROMPT.format(text=text),
                "stream": False,
                "keep_alive": "30m",
                "options": {"temperature": 0.0, "num_predict": 512},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        cleaned = str(response.json().get("response", "")).strip()
        cleaned = _strip_wrappers(cleaned)
        if not cleaned:
            log.warning("cleanup returned empty; using raw ASR")
            return text
        if len(cleaned) > max(40, int(len(text) * 1.8)):
            log.warning("cleanup expanded text too much; using raw ASR")
            return text
        log.info("cleanup: %s", cleaned[:120])
        return cleaned
    except httpx.TimeoutException:
        log.warning("cleanup timed out after %ss; using raw ASR", timeout)
        return text
    except Exception as exc:  # noqa: BLE001
        log.warning("cleanup failed (%s); using raw ASR", exc)
        return text


def _strip_wrappers(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    for prefix in ("cleaned:", "transcript:", "output:"):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
    return text
