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

    def fake_download(entry, destination):
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
