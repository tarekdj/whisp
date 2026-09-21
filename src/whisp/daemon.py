from __future__ import annotations

import argparse
import logging
import signal
import threading
import time
from pathlib import Path

from whisp.asr import Transcriber
from whisp.audio import make_recorder
from whisp.cleanup import cleanup_text
from whisp.config import load_config
from whisp.feedback import beep
from whisp.hotkey import HotkeyListener
from whisp.inject import Injector, focused_app_name, focused_is_vte
from whisp.stream import StreamSession, leftover_text, spaced_chunk

log = logging.getLogger("whisp")


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    if args.verbose:
        level = logging.DEBUG
    elif args.logs:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if args.command == "doctor":
        from whisp.doctor import run_doctor

        raise SystemExit(run_doctor())
    run_daemon(args)


def run_daemon(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    inject_mode = (
        "stdout"
        if args.stdout
        else "clipboard"
        if args.clipboard_only
        else cfg.inject
    )
    log.info("config %s", cfg.path)
    use_stream = cfg.stream if args.stream is None else args.stream
    log.info(
        "hotkey=%s whisper=%s ollama=%s inject=%s stream=%s",
        cfg.hotkey,
        cfg.whisper_model,
        cfg.ollama_model if cfg.cleanup else "off",
        inject_mode,
        "on" if use_stream else "off",
    )

    transcriber = Transcriber(cfg.whisper_model, sample_rate=cfg.sample_rate)
    recorder = make_recorder(cfg.sample_rate)
    injector = Injector(inject_mode, paste=cfg.paste)
    listener = HotkeyListener(cfg.hotkey, grab=cfg.grab_keyboard)
    streamer = (
        StreamSession(
            recorder,
            transcriber,
            injector,
            sample_rate=cfg.sample_rate,
            language=cfg.language,
            interval_s=cfg.stream_interval_s,
            cleanup=cfg.cleanup,
            ollama_host=cfg.ollama_host,
            ollama_model=cfg.ollama_model,
            cleanup_timeout_s=cfg.cleanup_timeout_s,
        )
        if use_stream
        else None
    )

    recording = False
    busy = False
    pressed_at = 0.0
    target_vte = False
    lock = threading.Lock()

    def on_press() -> None:
        nonlocal recording, pressed_at, target_vte
        with lock:
            if busy or recording:
                return
            recording = True
            pressed_at = time.monotonic()
        try:
            recorder.start()
        except Exception:
            with lock:
                recording = False
            log.exception("failed to start recording")
            beep("error", cfg.beep)
            return
        target_name = focused_app_name()
        target_vte = focused_is_vte(target_name)
        log.info("target %s (%s)", target_name or "unknown", "vte" if target_vte else "field")
        if streamer is not None:
            streamer.start(vte=target_vte)
        beep("start", cfg.beep)
        log.info("recording")

    def on_release() -> None:
        nonlocal recording, busy
        with lock:
            if not recording:
                return
            recording = False
            held_ms = (time.monotonic() - pressed_at) * 1000
            vte = target_vte
        committed = streamer.stop() if streamer is not None else ""
        pasted_any = streamer.pasted_any if streamer is not None else False
        try:
            audio = recorder.stop()
        except Exception:
            log.exception("failed to stop recording")
            beep("error", cfg.beep)
            return
        if held_ms < cfg.min_hold_ms:
            log.info("hold %.0fms < %sms; ignore", held_ms, cfg.min_hold_ms)
            beep("cancel", cfg.beep)
            return
        with lock:
            busy = True

        def work() -> None:
            nonlocal busy
            try:
                text = transcriber.transcribe(audio, language=cfg.language)
                if streamer is not None:
                    chunk = leftover_text(committed, text)
                    if not chunk and not pasted_any:
                        beep("cancel", cfg.beep)
                        return
                    if chunk:
                        if cfg.cleanup:
                            chunk = cleanup_text(
                                chunk,
                                host=cfg.ollama_host,
                                model=cfg.ollama_model,
                                timeout=cfg.cleanup_timeout_s,
                            )
                        injector.inject(
                            spaced_chunk(pasted_any, chunk),
                            vte=vte,
                            restore=True,
                        )
                else:
                    if not text:
                        beep("cancel", cfg.beep)
                        return
                    if cfg.cleanup:
                        text = cleanup_text(
                            text,
                            host=cfg.ollama_host,
                            model=cfg.ollama_model,
                            timeout=cfg.cleanup_timeout_s,
                        )
                    injector.inject(text, vte=vte, restore=True)
                beep("done", cfg.beep)
            except Exception:
                log.exception("processing failed")
                beep("error", cfg.beep)
            finally:
                with lock:
                    busy = False

        threading.Thread(target=work, daemon=True, name="whisp-process").start()

    def handle_stop(signum: int, _frame: object) -> None:
        log.info("received %s; shutting down", signal.Signals(signum).name)
        listener.stop()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    try:
        listener.start()
        log.info("ready — hold %s to dictate", cfg.hotkey)
        listener.run(on_press, on_release)
    finally:
        injector.close()
        listener.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="whisp",
        description="Local hold-to-talk dictation. Hold the hotkey, speak, release.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="config.toml path (default: ~/.config/whisp/config.toml)",
    )
    parser.add_argument("--stdout", action="store_true", help="print text instead of pasting")
    parser.add_argument(
        "--clipboard-only",
        action="store_true",
        help="copy to the clipboard without emitting a paste chord",
    )
    parser.add_argument(
        "--logs",
        action="store_true",
        help="print dictation logs (off by default)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="debug logs (implies --logs)",
    )
    stream = parser.add_mutually_exclusive_group()
    stream.add_argument(
        "--stream",
        dest="stream",
        action="store_true",
        help="paste cleaned batches while holding",
    )
    stream.add_argument(
        "--no-stream",
        dest="stream",
        action="store_false",
        help="disable live batches; one paste on release",
    )
    parser.set_defaults(stream=None)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["doctor"],
        help="doctor: check mic, hotkey, uinput, Ollama, clipboard",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
