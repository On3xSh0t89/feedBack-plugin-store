import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plugin_store_self_update_storelib", ROOT / "storelib.py")
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


def make_store(tmp_path, monkeypatch, version="0.3.0"):
    config = tmp_path / "config"
    plugin_root = tmp_path / "plugins"
    plugin_dir = plugin_root / "plugin_store"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps(
            {
                "id": "plugin_store",
                "name": "Plugin Store",
                "version": version,
                "homepage": "https://github.com/irnutsmurt/feedBack-plugin-store",
            }
        ),
        encoding="utf-8",
    )
    (plugin_dir / "old.txt").write_text("old", encoding="utf-8")
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugin_root))
    return storelib.PluginStore(config, plugin_dir, DummyLog()), plugin_dir


def archive_bytes(*, plugin_id="plugin_store", version="0.3.1"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "feedBack-plugin-store-main/plugin.json",
            json.dumps(
                {
                    "id": plugin_id,
                    "name": "Plugin Store",
                    "version": version,
                    "homepage": "https://github.com/irnutsmurt/feedBack-plugin-store",
                }
            ),
        )
        zf.writestr("feedBack-plugin-store-main/new.txt", "new")
        zf.writestr("feedBack-plugin-store-main/registry.yaml", "schema: 1\nplugins: []\n")
    return buf.getvalue()


def test_self_update_replaces_store_atomically(tmp_path, monkeypatch):
    store, plugin_dir = make_store(tmp_path, monkeypatch)
    store.self_update_status = lambda force=False: {
        "installed_version": "0.3.0",
        "available_version": "0.3.1",
        "update_available": True,
    }
    payload = archive_bytes()
    monkeypatch.setattr(storelib.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))

    result = store.install_self_update()

    assert result["version"] == "0.3.1"
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.3.1"
    assert (plugin_dir / "new.txt").read_text(encoding="utf-8") == "new"
    assert not (plugin_dir / "old.txt").exists()


def test_bad_self_update_never_replaces_installed_store(tmp_path, monkeypatch):
    store, plugin_dir = make_store(tmp_path, monkeypatch)
    store.self_update_status = lambda force=False: {
        "installed_version": "0.3.0",
        "available_version": "0.3.1",
        "update_available": True,
    }
    payload = archive_bytes(plugin_id="evil_plugin")
    monkeypatch.setattr(storelib.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))

    try:
        store.install_self_update()
    except storelib.StoreError:
        pass
    else:
        raise AssertionError("invalid self-update unexpectedly succeeded")

    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.3.0"
    assert (plugin_dir / "old.txt").read_text(encoding="utf-8") == "old"


def test_self_update_endpoint_schedules_restart_and_ui_is_one_click():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    html = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert '@app.post(f"{API_PREFIX}/self-update/install")' in routes
    assert "result = store.install_self_update()" in routes
    assert "schedule_restart()" in routes
    assert 'id="plugin-store-update-link"' in html
    assert ">Update Plugin Store</button>" in html
    assert 'api("/self-update/install", { method: "POST" })' in js
    assert "waitForNewInstance(result.instance_id)" in js
