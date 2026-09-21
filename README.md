# Whisp

Local hold-to-talk dictation for Linux, in the shape of Whisperflow: hold a
key, speak, release — cleaned text lands in whatever is focused (terminal,
TUI, textarea, address bar). Speech-to-text and cleanup both run on this
machine. Nothing is sent to the cloud.

```
hold Caps Lock → record → faster-whisper (CPU) → Ollama cleanup → clipboard paste
```

Built for **Ubuntu GNOME on Wayland**. Paste is clipboard-based (Unicode), not
character-by-character keycodes, so AZERTY accents and punctuation survive.

v1 is dictation only. Cleanup restores punctuation and casing and drops
fillers. It does not rewrite, translate, or run voice commands.

## How it works

1. **Hold** Caps Lock (or another evdev key). A short tap under 200 ms is ignored.
2. **Record** from the default mic (PortAudio / PipeWire; `pw-record` if that fails).
3. **Transcribe** with `faster-whisper` on CPU (`int8`). The `base` model
   downloads on first start (~150 MB) and stays in RAM.
4. **Clean** via a local Ollama model (`qwen2.5:1.5b` by default). If Ollama is
   down or times out (4 s), the raw transcript is pasted instead.
5. **Paste** with `wl-copy` plus a virtual-keyboard chord:
   - most GTK / Firefox fields, address bar → Shift+Insert
   - VTE terminals (gnome-terminal, Ptyxis, …) → Ctrl+Shift+V

Live batches are off by default. Set `stream = true` or start with `whisp --stream`
to paste cleaned word batches while you hold, then the leftover tail on release.

Caps Lock is grabbed and replayed so it does not toggle. If grab fails on a
device, that keyboard’s Caps Lock still toggles — disable Caps Lock in GNOME
Tweaks as a fallback.

## Requirements

- GNOME / Wayland, PipeWire
- Python 3.12+
- [Ollama](https://ollama.com) for optional transcript cleanup
- Membership in the `input` group (global hotkey + `/dev/uinput` paste)

`input` can read every keyboard. That is required for hold-to-talk (GNOME
shortcuts fire on press only). Treat this machine as single-user.

## Install

```bash
sudo apt install wl-clipboard python3-venv python3-gi gir1.2-atspi-2.0 libportaudio2

cd ~/whisp
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -e .

ollama pull qwen2.5:1.5b

sudo cp packaging/udev/99-whisp-uinput.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo usermod -aG input "$USER"
```

Log out of GNOME and back in (a new terminal is not enough), then:

```bash
source ~/whisp/.venv/bin/activate
whisp doctor    # should print "ready"
whisp           # hold Caps Lock, speak
```

### What the sudo commands do

None of these start the app. They are a one-time host setup so Whisp can hear
a global hotkey and paste like a keyboard.

| Command | Why |
| --- | --- |
| `sudo apt install wl-clipboard` | Installs `wl-copy` / `wl-paste`. On GNOME Wayland the clipboard is Wayland, not X11 (`xclip` is not enough). |
| `sudo cp …/99-whisp-uinput.rules /etc/udev/rules.d/` | Makes `/dev/uinput` `root:input` mode `0660`. Without this, the node stays `root:root` `600` and you still cannot open it after joining `input`. |
| `sudo udevadm control --reload-rules && sudo udevadm trigger` | Reload reads the new rule; trigger applies it to the existing `/dev/uinput` node (no reboot). |
| `sudo usermod -aG input "$USER"` | Appends you to `input` so you can read `/dev/input/event*` (Caps Lock hold/release) and write `/dev/uinput` (paste chord). |

Whisp uses the virtual keyboard only to tap **Shift+Insert** or
**Ctrl+Shift+V** after text is on the clipboard. It does not type
character-by-character.

`ollama pull qwen2.5:1.5b` is not sudo; it only downloads the cleanup model.

### systemd user unit

```bash
mkdir -p ~/.config/systemd/user
cp packaging/systemd/whisp.service ~/.config/systemd/user/
# edit ExecStart if the repo is not ~/whisp
systemctl --user daemon-reload
systemctl --user enable --now whisp
```

## Usage

Hold **Caps Lock**, speak, release. Text is cleaned and pasted once.

With `stream = true` (or `whisp --stream`), cleaned word batches appear in the
focused field while you hold; the leftover tail pastes when you release.

| Target | How text lands |
| --- | --- |
| Most GTK/Firefox fields, address bar | `wl-copy` + Shift+Insert |
| VTE terminals (gnome-terminal, Ptyxis, …) | Ctrl+Shift+V |
| `--clipboard-only` | clipboard only |
| `--stdout` | printed to the daemon’s stdout |

Vim / modal editors: dictation always inserts characters. Focus insert mode or
a plain text field first. Whisp does not detect normal mode.

```bash
whisp                 # daemon (quiet)
whisp --stream        # live batches while holding
whisp --no-stream     # force one paste on release
whisp --logs          # INFO logs
whisp -v              # debug logs
whisp doctor          # permissions, mic, Ollama, clipboard
whisp --stdout        # print instead of paste
whisp --clipboard-only
```

## Config

Written on first run to `~/.config/whisp/config.toml` (`WHISP_CONFIG` or
`--config` overrides the path):

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `KEY_CAPSLOCK` | evdev key name |
| `whisper_model` | `Systran/faster-whisper-base` | `small` is slower and more accurate |
| `ollama_host` | `http://127.0.0.1:11434` | local Ollama |
| `ollama_model` | `qwen2.5:1.5b` | cleanup model |
| `cleanup` | `true` | if Ollama fails or times out, raw ASR is pasted |
| `inject` | `paste` | `paste` / `clipboard` / `stdout` |
| `paste` | `auto` | `auto` / `shift+insert` / `ctrl+shift+v` |
| `beep` | `true` | start / done / cancel / error sounds |
| `grab_keyboard` | `true` | intercept the hotkey so Caps Lock does not toggle |
| `language` | `""` (auto) | set `fr` or `en` to skip detection |
| `min_hold_ms` | `200` | ignore accidental taps |
| `cleanup_timeout_s` | `4.0` | fall back to raw ASR after this |
| `stream` | `false` | `true` pastes cleaned batches while holding; default is one paste on release |
| `stream_interval_s` | `1.0` | how often a new frozen batch is transcribed |

Cleanup **does not rewrite**. It restores punctuation/casing and drops fillers
(`um`, `uh`, `euh`, …). Voice commands and “make this an email” modes are out
of v1.

## Troubleshooting

**`whisp doctor` says not in `input`, `/dev/uinput` not writable, no keyboards**

Host files can be correct while this process still lacks the group. `usermod`
does not apply until login. Check `id` for `107(input)`. If `/etc/group` lists
you but `id` does not, log out of GNOME (or restart the session that will run
`whisp`). A new terminal in an old session is not enough.

**Grab warning / Caps Lock still toggles**

Grab failed on that device. Disable Caps Lock in GNOME Tweaks, or set
`grab_keyboard = false` and pick another `hotkey`.

**Ollama warn, cleanup skipped**

ASR still pastes. Pull the model: `ollama pull qwen2.5:1.5b`.

**Beep on hold, but nothing is pasted**

The start beep is playback, not proof the mic is capturing. If the log says
`VAD filter removed` the whole clip and `asr …: (empty)`, the input is silence
or a dead jack.

Headphones often switch capture to **Headset Mic**. A headphone-only jack has
no microphone, so Whisper sees DC and drops the clip.

```bash
amixer -c 0 sset 'Internal Mic' cap
```

Or GNOME Settings → Sound → Input → internal microphone. Then hold Caps Lock
again (no need to restart `whisp`). Logs should show a non-empty `asr …:` line
and a done beep.

## Acceptance

After `whisp doctor` prints `ready`: hold Caps Lock, speak, and the text should
appear in gedit, in gnome-terminal, and in the Firefox address bar.

With `stream = true`, batches should appear while you speak and the tail on
release.

## License

MIT
