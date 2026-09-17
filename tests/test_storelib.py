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


def _write_plugin(folder, plugin_id, version, **extra):
    folder.mkdir(parents=True)
    (folder / "plugin.json").write_text(
        json.dumps({"id": plugin_id, "name": plugin_id, "version": version, **extra}),
        encoding="utf-8",
    )


def _note_store(tmp_path, monkeypatch, builtin_version=None, **builtin_extra):
    config = tmp_path / "config"
    plugin_root = tmp_path / "user-plugins"
    builtin_root = tmp_path / "builtin"
    builtin_root.mkdir()
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugin_root))
    store = storelib.PluginStore(config, ROOT, DummyLog(), builtin_root=builtin_root)
    catalog_version = store._entry("note_detect")[0]["version"]
    if builtin_version is not None:
        version = catalog_version if builtin_version == "same" else builtin_version
        _write_plugin(builtin_root / "notedetect", "note_detect", version, **builtin_extra)
    return store, plugin_root, builtin_root, catalog_version


def _note_state(store):
    return next(p for p in store.catalog()["plugins"] if p["id"] == "note_detect")


def _fake_note_download(store, tmp_path, monkeypatch, version):
    archive = tmp_path / "note.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            "feedBack-plugin-notedetect-main/plugin.json",
            json.dumps({"id": "note_detect", "name": "Note Detection", "version": version}),
        )

    def fake_download(entry, destination, **kwargs):
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr(store, "_download", fake_download)


def test_builtin_plugin_is_reported_installed(tmp_path, monkeypatch):
    # Folder name differs from the id, as it does for feedBack's notedetect.
    store, _plugins, builtin_root, version = _note_store(tmp_path, monkeypatch, "same")
    state = _note_state(store)
    assert state["installed"] is True
    assert state["status"] == "installed"
    assert state["install_source"] == "builtin"
    assert state["install_path"] == str(builtin_root / "notedetect")
    assert state["installed_version"] == version
    assert state["can_install"] is False
    assert state["can_remove"] is False
    assert state["can_update"] is False
    with pytest.raises(storelib.StoreError):
        store.remove("note_detect")


def test_outdated_builtin_updates_into_user_folder(tmp_path, monkeypatch):
    store, plugin_root, builtin_root, version = _note_store(tmp_path, monkeypatch, "0.0.1")
    state = _note_state(store)
    assert state["status"] == "update_available"
    assert state["can_update"] is True

    _fake_note_download(store, tmp_path, monkeypatch, version)
    result = store.install("note_detect", replace=True)
    assert result["ok"] is True
    assert (plugin_root / "note_detect" / "plugin.json").is_file()
    assert (builtin_root / "notedetect" / "plugin.json").is_file()

    state = _note_state(store)
    assert state["install_source"] == "store"
    assert state["status"] == "installed"
    assert state["can_remove"] is True


def test_locked_bundled_copy_is_not_offered_an_update(tmp_path, monkeypatch):
    config = tmp_path / "config"
    plugin_root = tmp_path / "user-plugins"
    builtin_root = tmp_path / "builtin"
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugin_root))
    # The host always keeps a bundled:true copy that lives at builtin/<id>.
    _write_plugin(builtin_root / "note_detect", "note_detect", "0.0.1", bundled=True)
    store = storelib.PluginStore(config, ROOT, DummyLog(), builtin_root=builtin_root)
    state = _note_state(store)
    assert state["installed"] is True
    assert state["builtin_locked"] is True
    assert state["can_update"] is False
    with pytest.raises(storelib.StoreError):
        store.install("note_detect", replace=True)
    assert not (plugin_root / "note_detect").exists()


def test_manual_clone_is_recognised_and_left_alone(tmp_path, monkeypatch):
    store, plugin_root, _builtin, version = _note_store(tmp_path, monkeypatch, "0.0.1")
    clone = plugin_root / "feedBack-plugin-notedetect"
    _write_plugin(clone, "note_detect", "0.0.2")
    state = _note_state(store)
    # The user's copy is loaded ahead of the bundled one, so it is what counts.
    assert state["installed"] is True
    assert state["install_source"] == "manual"
    assert state["install_path"] == str(clone)
    assert state["status"] == "installed_external"
    assert state["catalog_newer"] is True
    assert state["can_update"] is False
    assert state["can_remove"] is False

    _fake_note_download(store, tmp_path, monkeypatch, version)
    for replace in (False, True):
        with pytest.raises(storelib.StoreError):
            store.install("note_detect", replace=replace)
    with pytest.raises(storelib.StoreError):
        store.remove("note_detect")
    assert not (plugin_root / "note_detect").exists()
    assert (clone / "plugin.json").is_file()
    assert store.set_excluded("note_detect", True)["excluded"] is True


def test_not_installed_anywhere_is_available(tmp_path, monkeypatch):
    store, _plugins, _builtin, _version = _note_store(tmp_path, monkeypatch)
    state = _note_state(store)
    assert state["installed"] is False
    assert state["install_source"] is None
    assert state["can_install"] is True


def test_builtin_dir_discovered_from_host_loader(tmp_path, monkeypatch):
    import types

    monkeypatch.delenv("FEEDBACK_BUILTIN_PLUGINS_DIR", raising=False)
    host = types.ModuleType("plugins")
    host.PLUGINS_DIR = tmp_path
    host.load_plugins = lambda *a, **k: None
    saved = sys.modules.get("plugins")
    sys.modules["plugins"] = host
    try:
        assert storelib.discover_builtin_plugins_dir() == tmp_path.resolve()
        # An unrelated module named "plugins" is not mistaken for the host.
        sys.modules["plugins"] = types.ModuleType("plugins")
        assert storelib.discover_builtin_plugins_dir() is None
    finally:
        if saved is None:
            sys.modules.pop("plugins", None)
        else:
            sys.modules["plugins"] = saved
