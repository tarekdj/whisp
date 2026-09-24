from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from whisp.audio import (
    PipewireRecorder,
    SoundDeviceRecorder,
    _wpctl_default_source_label,
    default_sounddevice_input,
    make_recorder,
    pipewire_default_capture,
)


def test_default_sounddevice_input_resolves_index_and_name():
    sd = SimpleNamespace(
        default=SimpleNamespace(device=(7, None)),
        query_devices=lambda idx: {"name": f"Mic {idx}"},
    )
    idx, label = default_sounddevice_input(sd)
    assert idx == 7
    assert label == "[7] Mic 7"


def test_default_sounddevice_input_unknown_when_no_default():
    sd = SimpleNamespace(default=SimpleNamespace(device=(None, None)))
    idx, label = default_sounddevice_input(sd)
    assert idx is None
    assert label == "unknown"


def test_pipewire_default_capture_uses_pactl():
    with (
        patch("whisp.audio.shutil.which", return_value="/usr/bin/pactl"),
        patch(
            "whisp.audio.subprocess.check_output",
            return_value="alsa_input.usb-headset\n",
        ),
    ):
        target, label = pipewire_default_capture()
    assert target == "alsa_input.usb-headset"
    assert label == "alsa_input.usb-headset"


def test_wpctl_default_source_label_parses_starred_source():
    status = """
Audio
 ├─ Sources:
 │  *   52. Built-in Audio Stéréo analogique  [vol: 1.00 MUTED]
 ├─ Source endpoints:
"""
    with patch("whisp.audio.shutil.which", return_value="/usr/bin/wpctl"), patch(
        "whisp.audio.subprocess.check_output",
        return_value=status,
    ):
        assert _wpctl_default_source_label() == "[52] Built-in Audio Stéréo analogique"


def test_pipewire_default_capture_uses_wpctl_when_no_pactl():
    status = " ├─ Sources:\n │  *   52. Internal Mic  [vol: 1.00]\n"
    with patch("whisp.audio.shutil.which") as which, patch(
        "whisp.audio.subprocess.check_output",
        return_value=status,
    ):
        which.side_effect = lambda cmd: None if cmd == "pactl" else "/usr/bin/wpctl"
        target, label = pipewire_default_capture()
    assert target == "@DEFAULT_AUDIO_SOURCE@"
    assert label == "[52] Internal Mic"


def test_make_recorder_prefers_pw_record_when_pactl_default_exists():
    with (
        patch("whisp.audio.shutil.which", return_value="/usr/bin/pw-record"),
        patch(
            "whisp.audio.pipewire_default_capture",
            return_value=("alsa_input.test", "alsa_input.test"),
        ),
        patch.object(PipewireRecorder, "start"),
        patch.object(PipewireRecorder, "stop", return_value=np.zeros(0, dtype=np.float32)),
    ):
        rec = make_recorder(16000)
    assert isinstance(rec, PipewireRecorder)


def test_make_recorder_falls_back_to_pw_when_sounddevice_silent():
    def fake_which(name: str) -> str | None:
        return "/usr/bin/pw-record" if name == "pw-record" else None

    with (
        patch("whisp.audio.shutil.which", side_effect=fake_which),
        patch("whisp.audio.pipewire_default_capture", return_value=(None, "auto")),
        patch("whisp.audio._probe_sounddevice", return_value=0.0),
        patch("whisp.audio._probe_pipewire"),
    ):
        rec = make_recorder(16000)
    assert isinstance(rec, PipewireRecorder)
