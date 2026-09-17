import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plugin_store_storelib", ROOT / "storelib.py")
storelib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storelib
SPEC.loader.exec_module(storelib)


def test_registry_accepts_official_plugin():
    registry = storelib.load_registry_yaml(
        """
schema: 1
plugins:
  - id: note_detect
    name: Note Detection
    description: Test
    version: 1.2.3
    repository: https://github.com/got-feedBack/feedBack-plugin-notedetect
    ref: main
"""
    )
    assert registry["plugins"][0]["id"] == "note_detect"


def test_registry_rejects_non_official_repo():
    with pytest.raises(storelib.StoreError):
        storelib.load_registry_yaml(
            """
schema: 1
plugins:
  - id: bad
    name: Bad
    description: Test
    version: 1.0.0
    repository: https://github.com/example/bad
    ref: main
"""
        )


def test_zip_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.txt", "nope")

    with pytest.raises(storelib.StoreError):
        storelib.safe_extract_zip(
            archive,
            tmp_path / "out",
            max_extract_bytes=1024 * 1024,
            max_files=10,
        )


def test_version_comparison():
    assert storelib.compare_versions("1.0.0", "1.0.1") == -1
    assert storelib.compare_versions("1.0.1", "1.0.1") == 0
    assert storelib.compare_versions("1.1.0", "1.0.9") == 1
    assert storelib.compare_versions("1.0.0-beta.1", "1.0.0") == -1


def test_locate_top_level_plugin(tmp_path):
    plugin = tmp_path / "repo-main"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        json.dumps({"id": "example", "name": "Example"}),
        encoding="utf-8",
    )
    assert storelib.locate_plugin_root(tmp_path) == plugin

class DummyLog:
    def info(self, *args, **kwargs):
        pass
    def error(self, *args, **kwargs):
        pass


def test_install_and_remove_cycle(tmp_path, monkeypatch):
    config = tmp_path / "config"
    plugin_root = config / "user-plugins"
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugin_root))

    store = storelib.PluginStore(config, ROOT, DummyLog())

    archive_template = tmp_path / "note.zip"
    manifest = {
        "id": "note_detect",
        "name": "Note Detection",
        "version": "1.32.0",
    }
    with zipfile.ZipFile(archive_template, "w") as zf:
        zf.writestr("feedBack-plugin-notedetect-main/plugin.json", json.dumps(manifest))
        zf.writestr("feedBack-plugin-notedetect-main/screen.js", "// test")

    def fake_download(entry, destination, **kwargs):
        destination.write_bytes(archive_template.read_bytes())

    monkeypatch.setattr(store, "_download", fake_download)

    result = store.install("note_detect", replace=False)
    assert result["ok"] is True
    assert (plugin_root / "note_detect" / "plugin.json").is_file()

    catalog = store.catalog()
    note = next(p for p in catalog["plugins"] if p["id"] == "note_detect")
    assert note["status"] == "installed"

    removed = store.remove("note_detect")
    assert removed["ok"] is True
    assert not (plugin_root / "note_detect").exists()


def test_plugin_root_may_live_outside_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    plugin_root = tmp_path / "user-plugins"
    plugin_dir = tmp_path / "plugin_store"
    config_dir.mkdir()
    plugin_dir.mkdir()
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugin_root))

    class Log:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    store = storelib.PluginStore(config_dir=config_dir, plugin_dir=plugin_dir, log=Log())
    assert store.plugin_root == plugin_root.resolve()
    assert store.state_dir == (config_dir / "plugin_store").resolve()


def _make_store(tmp_path):
    config_dir = tmp_path / "config"
    plugin_dir = tmp_path / "plugin_store"
    config_dir.mkdir()
    plugin_dir.mkdir()
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")

    class Log:
        def info(self, *args, **kwargs): pass
        def error(self, *args, **kwargs): pass

    return config_dir, storelib.PluginStore(config_dir=config_dir, plugin_dir=plugin_dir, log=Log())


def test_plugin_root_honours_legacy_slopsmith_env(tmp_path, monkeypatch):
    # The feedBack desktop app exports only SLOPSMITH_PLUGINS_DIR.
    plugin_root = tmp_path / "desktop-plugins"
    monkeypatch.delenv("FEEDBACK_PLUGINS_DIR", raising=False)
    monkeypatch.setenv("SLOPSMITH_PLUGINS_DIR", str(plugin_root))
    _, store = _make_store(tmp_path)
    assert store.plugin_root == plugin_root.resolve()


def test_feedback_plugins_dir_wins_over_legacy_env(tmp_path, monkeypatch):
    preferred = tmp_path / "preferred"
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(preferred))
    monkeypatch.setenv("SLOPSMITH_PLUGINS_DIR", str(tmp_path / "legacy"))
    _, store = _make_store(tmp_path)
    assert store.plugin_root == preferred.resolve()


def test_plugin_root_defaults_under_config_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FEEDBACK_PLUGINS_DIR", raising=False)
    monkeypatch.delenv("SLOPSMITH_PLUGINS_DIR", raising=False)
    config_dir, store = _make_store(tmp_path)
    assert store.plugin_root == (config_dir / "user-plugins").resolve()


def test_registry_url_falls_back_to_manifest_homepage(tmp_path):
    (tmp_path / "plugin.json").write_text(
        json.dumps({"homepage": "https://github.com/irnutsmurt/feedBack-plugin-store"}),
        encoding="utf-8",
    )
    assert storelib.discover_registry_url(tmp_path) == (
        "https://raw.githubusercontent.com/irnutsmurt/feedBack-plugin-store/main/registry.yaml"
    )
