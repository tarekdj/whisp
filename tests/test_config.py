import pytest

from whisp.config import load_config


def test_migration_appends_only_missing_keys(tmp_path):
    # Config already has stream_interval_s but not stream: appending both
    # would duplicate a key and corrupt the file.
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('hotkey = "KEY_CAPSLOCK"\nstream_interval_s = 2.0\n')
    cfg = load_config(cfg_path)
    assert cfg.stream is False
    assert cfg.stream_interval_s == 2.0
    # File stays parseable and contains each key exactly once.
    text = cfg_path.read_text()
    assert text.count("stream_interval_s") == 1
    assert text.count("stream =") == 1


def test_migration_appends_both_when_neither_present(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('hotkey = "KEY_CAPSLOCK"\n')
    cfg = load_config(cfg_path)
    assert cfg.stream is False
    assert cfg.stream_interval_s == 1.0


def test_migration_noop_when_both_present(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("stream = true\nstream_interval_s = 0.5\n")
    before = cfg_path.read_text()
    cfg = load_config(cfg_path)
    assert cfg.stream is True
    assert cfg.stream_interval_s == 0.5
    assert cfg_path.read_text() == before


def test_fresh_default_written(tmp_path):
    cfg_path = tmp_path / "sub" / "config.toml"
    cfg = load_config(cfg_path)
    assert cfg.stream is False
    assert cfg_path.exists()


def test_invalid_inject_rejected(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text('inject = "telepathy"\n')
    with pytest.raises(ValueError, match="inject"):
        load_config(cfg_path)
