"""settings: Config/Tuning defaults, merging bundled + user config.json, saving user settings."""
from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from pixelcat import paths, settings
from pixelcat.settings import Config, Tuning, load_config


@pytest.fixture
def places(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Path]:
    """A frozen build: bundle (lowest), next to the exe, then %APPDATA%\\PixelCat (highest)."""
    bundle, exe_dir = tmp_path / "bundle", tmp_path / "Programs" / "PixelCat"
    bundle.mkdir()
    exe_dir.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "PixelCat.exe"))
    monkeypatch.delenv("PIXELCAT_MODEL", raising=False)
    return {"bundle": bundle / "config.json", "exe": exe_dir.resolve() / "config.json",
            "user": paths.data_dir() / "config.json"}


def write(path: Path, data) -> None:  # type: ignore[no-untyped-def]
    path.write_text(json.dumps(data), "utf-8")


# ---------------------------------------------------------------- load_config
def test_no_config_files_gives_the_defaults(places) -> None:  # type: ignore[no-untyped-def]
    assert asdict(load_config()) == asdict(Config())


def test_later_files_override_earlier_ones(places) -> None:  # type: ignore[no-untyped-def]
    write(places["bundle"], {"model": "bundle-model", "temperature": 0.1, "num_ctx": 1000})
    write(places["exe"], {"model": "exe-model", "temperature": 0.2})
    write(places["user"], {"model": "user-model"})
    cfg = load_config()
    assert (cfg.model, cfg.temperature, cfg.num_ctx) == ("user-model", 0.2, 1000)


def test_tuning_is_merged_field_by_field(places) -> None:  # type: ignore[no-untyped-def]
    write(places["bundle"], {"tuning": {"walk_speed": 20.0, "min_travel_gap_s": 50.0}})
    write(places["user"], {"tuning": {"walk_speed": 25.0}})
    t = load_config().tuning
    assert (t.walk_speed, t.min_travel_gap_s, t.run_speed) == (25.0, 50.0, Tuning().run_speed)


def test_lists_become_tuples_for_tuple_fields(places) -> None:  # type: ignore[no-untyped-def]
    write(places["user"], {"tuning": {"blink_every": [1.0, 2.0]}})
    assert load_config().tuning.blink_every == (1.0, 2.0)


def test_dicts_and_lists_replace_the_defaults(places) -> None:  # type: ignore[no-untyped-def]
    write(places["user"], {"apps": {"notepad": ["notepad.exe"]}, "allowed_roots": ["desktop"]})
    cfg = load_config()
    assert cfg.apps == {"notepad": ["notepad.exe"]} and cfg.allowed_roots == ["desktop"]


def test_unknown_keys_are_ignored_with_a_warning(places, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    write(places["user"], {"no_such_option": 1, "tuning": {"no_such_tuning": 2}, "model": "m"})
    with caplog.at_level(logging.WARNING, logger="pixelcat.settings"):
        cfg = load_config()
    assert cfg.model == "m"
    assert not hasattr(cfg, "no_such_option") and not hasattr(cfg.tuning, "no_such_tuning")
    assert "no_such_option" in caplog.text and "no_such_tuning" in caplog.text


@pytest.mark.parametrize("content", ["{broken", "[1, 2]", "\"text\"", "", "null"])
def test_a_broken_config_file_is_skipped(places, content: str) -> None:  # type: ignore[no-untyped-def]
    write(places["bundle"], {"model": "bundle-model"})
    places["user"].write_text(content, "utf-8")
    assert load_config().model == "bundle-model"


def test_a_config_that_is_a_folder_is_skipped(places) -> None:  # type: ignore[no-untyped-def]
    places["user"].mkdir()
    assert load_config().model == Config().model


def test_pixelcat_model_env_overrides_everything(places, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    write(places["user"], {"model": "user-model"})
    monkeypatch.setenv("PIXELCAT_MODEL", "qwen3.5:0.8b")
    assert load_config().model == "qwen3.5:0.8b"


def test_the_repos_config_json_loads_from_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delenv("PIXELCAT_MODEL", raising=False)
    bundled = json.loads((paths.app_dir() / "config.json").read_text("utf-8"))
    cfg = load_config()
    assert cfg.model == bundled["model"]
    assert cfg.tuning.min_travel_gap_s == bundled["tuning"]["min_travel_gap_s"]


def test_defaults_are_independent_instances() -> None:
    a, b = Config(), Config()
    a.apps["x"] = ["x.exe"]
    a.allowed_roots.append("c:")
    a.tuning.walk_speed = 1.0
    assert "x" not in b.apps and "c:" not in b.allowed_roots and b.tuning.walk_speed != 1.0


# ---------------------------------------------------------------- user settings
def test_save_user_setting_writes_the_appdata_config() -> None:
    settings.save_user_setting("model", "qwen3.5:9b")
    path = paths.data_dir() / "config.json"
    assert json.loads(path.read_text("utf-8")) == {"model": "qwen3.5:9b"}
    assert settings.user_settings() == {"model": "qwen3.5:9b"}


def test_saved_user_settings_win_in_load_config(places) -> None:  # type: ignore[no-untyped-def]
    write(places["exe"], {"model": "exe-model", "keep_alive": "5m"})
    settings.save_user_setting("model", "qwen3.5:4b")
    cfg = load_config()
    assert (cfg.model, cfg.keep_alive) == ("qwen3.5:4b", "5m")


def test_save_user_settings_merges_with_what_is_there() -> None:
    settings.save_user_settings({"model": "a", "keep_alive": "3m"})
    settings.save_user_settings({"model": "b", "preload_on_start": False})
    assert settings.user_settings() == {"model": "b", "keep_alive": "3m", "preload_on_start": False}
    text = (paths.data_dir() / "config.json").read_text("utf-8")
    assert text == json.dumps({"model": "b", "keep_alive": "3m", "preload_on_start": False}, indent=2)
    assert not list(paths.data_dir().glob("*.tmp"))


def test_save_user_settings_into_another_folder(tmp_path: Path) -> None:
    folder = tmp_path / "installed" / "PixelCat"
    settings.save_user_settings({"ai_mode": "online"}, folder)
    assert json.loads((folder / "config.json").read_text("utf-8")) == {"ai_mode": "online"}
    assert settings.user_settings(folder) == {"ai_mode": "online"}
    assert settings.user_settings() == {}


def test_save_user_settings_replaces_a_broken_file() -> None:
    path = paths.data_dir() / "config.json"
    path.write_text("{broken", "utf-8")
    settings.save_user_settings({"model": "m"})
    assert json.loads(path.read_text("utf-8")) == {"model": "m"}


def test_user_settings_missing_is_empty(tmp_path: Path) -> None:
    assert settings.user_settings(tmp_path / "nowhere") == {}
