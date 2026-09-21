from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "whisp" / "config.toml"

DEFAULT_TOML = """\
# Whisp — local hold-to-talk dictation
hotkey = "KEY_CAPSLOCK"
whisper_model = "Systran/faster-whisper-base"
ollama_host = "http://127.0.0.1:11434"
ollama_model = "qwen2.5:1.5b"
cleanup = true
# paste | clipboard | stdout
inject = "paste"
# auto | shift+insert | ctrl+shift+v
paste = "auto"
beep = true
grab_keyboard = true
min_hold_ms = 200
cleanup_timeout_s = 4.0
sample_rate = 16000
# empty = auto-detect (needed for mixed FR/EN)
language = ""
"""

VALID_INJECT = {"paste", "clipboard", "stdout"}
VALID_PASTE = {"auto", "shift+insert", "ctrl+shift+v"}


@dataclass(frozen=True)
class Config:
    hotkey: str = "KEY_CAPSLOCK"
    whisper_model: str = "Systran/faster-whisper-base"
    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:1.5b"
    cleanup: bool = True
    inject: str = "paste"
    paste: str = "auto"
    beep: bool = True
    grab_keyboard: bool = True
    min_hold_ms: int = 200
    cleanup_timeout_s: float = 4.0
    sample_rate: int = 16000
    language: str = ""
    path: Path = DEFAULT_PATH


def load_config(path: Path | None = None, *, write_default: bool = True) -> Config:
    path = path or Path(os.environ.get("WHISP_CONFIG", DEFAULT_PATH))
    if not path.exists():
        if not write_default:
            return Config(path=path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_TOML, encoding="utf-8")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    inject = str(data.get("inject", "paste"))
    paste = str(data.get("paste", "auto"))
    if inject not in VALID_INJECT:
        raise ValueError(f"inject must be one of {sorted(VALID_INJECT)}, got {inject!r}")
    if paste not in VALID_PASTE:
        raise ValueError(f"paste must be one of {sorted(VALID_PASTE)}, got {paste!r}")
    language = str(data.get("language", "") or "")
    return Config(
        hotkey=str(data.get("hotkey", "KEY_CAPSLOCK")),
        whisper_model=str(data.get("whisper_model", "Systran/faster-whisper-base")),
        ollama_host=str(data.get("ollama_host", "http://127.0.0.1:11434")).rstrip("/"),
        ollama_model=str(data.get("ollama_model", "qwen2.5:1.5b")),
        cleanup=bool(data.get("cleanup", True)),
        inject=inject,
        paste=paste,
        beep=bool(data.get("beep", True)),
        grab_keyboard=bool(data.get("grab_keyboard", True)),
        min_hold_ms=int(data.get("min_hold_ms", 200)),
        cleanup_timeout_s=float(data.get("cleanup_timeout_s", 4.0)),
        sample_rate=int(data.get("sample_rate", 16000)),
        language=language,
        path=path,
    )
