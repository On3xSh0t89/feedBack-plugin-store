import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plugin_store_v030_storelib", ROOT / "storelib.py")
storelib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storelib
SPEC.loader.exec_module(storelib)


class DummyLog:
    def info(self, *args, **kwargs):
        pass
    def error(self, *args, **kwargs):
        pass
    def exception(self, *args, **kwargs):
        pass


def write_plugin(path: Path, version: str):
    path.mkdir(parents=True, exist_ok=True)
    (path / "plugin.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "name": "Demo",
                "version": version,
                "description": "Demo plugin",
                "type": "tool",
                "category": "tools",
            }
        ),
        encoding="utf-8",
    )
    (path / "payload.txt").write_text(version, encoding="utf-8")


def build_store(tmp_path: Path, monkeypatch, backups: int = 2):
    config = tmp_path / "config"
    plugin_root = tmp_path / "plugins"
    plugin_dir = tmp_path / "plugin_store"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": "plugin_store",
                "name": "Plugin Store",
                "version": "0.3.0",
                "homepage": "https://github.com/irnutsmurt/feedBack-plugin-store",
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugin_root))
    monkeypatch.setenv("FEEDBACK_PLUGIN_STORE_BACKUPS_PER_PLUGIN", str(backups))
    return storelib.PluginStore(config, plugin_dir, DummyLog()), plugin_root


def test_snapshot_is_bounded(tmp_path, monkeypatch):
    store, plugin_root = build_store(tmp_path, monkeypatch, backups=2)
    target = plugin_root / "demo"

    write_plugin(target, "1.0.0")
    store._snapshot_backup("demo", target, None)

    (target / "plugin.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "version": "2.0.0"}),
        encoding="utf-8",
    )
    store._snapshot_backup("demo", target, None)

    (target / "plugin.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "version": "3.0.0"}),
        encoding="utf-8",
    )
    store._snapshot_backup("demo", target, None)

    records = store._backup_records("demo")
    assert len(records) == 2
    assert {item["version"] for item in records} == {"2.0.0", "3.0.0"}


def test_rollback_works_with_retention_one(tmp_path, monkeypatch):
    store, plugin_root = build_store(tmp_path, monkeypatch, backups=1)
    target = plugin_root / "demo"

    write_plugin(target, "1.0.0")
    store._snapshot_backup("demo", target, None)

    # Simulate the installed updated version.
    for child in target.iterdir():
        child.unlink()
    target.rmdir()
    write_plugin(target, "2.0.0")

    result = store.rollback("demo")

    assert result["version"] == "1.0.0"
    manifest = json.loads((target / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.0"
    assert (target / "payload.txt").read_text(encoding="utf-8") == "1.0.0"

    # Current 2.0.0 was snapshotted first, so rollback remains reversible.
    records = store._backup_records("demo")
    assert len(records) == 1
    assert records[0]["version"] == "2.0.0"


def test_install_marks_success_so_rollback_snapshot_is_retained():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    install = src[src.index("    def _install_entry("):src.index("    def install(", src.index("    def _install_entry("))]
    assert "update_committed = False" in install
    assert "self._save_managed(managed)\n            update_committed = True" in install
    assert "not update_committed" in install
