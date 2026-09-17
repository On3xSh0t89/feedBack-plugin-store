import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plugin_store_storelib_v040", ROOT / "storelib.py")
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


def build_store(tmp_path, monkeypatch):
    config = tmp_path / "config"
    plugins = tmp_path / "plugins"
    plugin_dir = tmp_path / "plugin_store"
    plugin_dir.mkdir()
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "id": "plugin_store",
            "name": "Plugin Store",
            "version": "0.4.0",
            "homepage": "https://github.com/irnutsmurt/feedBack-plugin-store",
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugins))
    monkeypatch.setenv("FEEDBACK_HOST_VERSION", "1.0.0")
    return storelib.PluginStore(config, plugin_dir, DummyLog()), plugins


def test_manifest_is_v040():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3"


def test_tag_archive_uses_tags_namespace():
    url = storelib.archive_url({
        "id": "demo",
        "repository": "https://github.com/example/demo",
        "ref": "v1.2.3",
        "ref_kind": "tag",
    })
    assert "/zip/refs/tags/v1.2.3" in url


def test_exclusions_persist(tmp_path, monkeypatch):
    store, plugins = build_store(tmp_path, monkeypatch)
    target = plugins / "demo"
    target.mkdir(parents=True)
    (target / "plugin.json").write_text(
        json.dumps({"id": "demo", "name": "Demo", "version": "1.0.0"}),
        encoding="utf-8",
    )

    result = store.set_excluded("demo", True)
    assert result["excluded"] is True

    saved = json.loads(store.exclusions_path.read_text(encoding="utf-8"))
    assert saved["plugins"] == ["demo"]

    result = store.set_excluded("demo", False)
    assert result["excluded"] is False
    assert store._exclusions() == set()


def test_update_all_skips_excluded_plugins(tmp_path, monkeypatch):
    store, _plugins = build_store(tmp_path, monkeypatch)

    monkeypatch.setattr(store, "catalog", lambda force_refresh=False: {
        "stores": [{
            "id": "official",
            "third_party": False,
            "name": "Official",
            "plugins": [
                {
                    "id": "skip_me",
                    "name": "Skip",
                    "status": "update_available",
                    "can_update": True,
                    "excluded": True,
                },
                {
                    "id": "update_me",
                    "name": "Update",
                    "status": "update_available",
                    "can_update": True,
                    "excluded": False,
                },
            ],
        }]
    })

    called = []

    def fake_install(plugin_id, **kwargs):
        called.append(plugin_id)
        return {"version": "2.0.0"}

    monkeypatch.setattr(store, "install", fake_install)
    result = store.update_all()

    assert called == ["update_me"]
    assert result["updated_count"] == 1


def test_direct_github_inspection_uses_manifest_and_compatibility(tmp_path, monkeypatch):
    store, _plugins = build_store(tmp_path, monkeypatch)
    monkeypatch.setattr(store, "_official_ids", lambda: set())
    monkeypatch.setattr(store, "_other_third_party_ids", lambda exclude_store_id=None: set())
    monkeypatch.setattr(store, "_fetch_repo_manifest", lambda repo, ref, plugin_id_hint="plugin": {
        "id": "community_demo",
        "name": "Community Demo",
        "description": "Test plugin",
        "version": "1.2.3",
        "minHost": "0.9.0",
    })

    entry = store.inspect_github_plugin("https://github.com/example/community-demo")
    assert entry["id"] == "community_demo"
    assert entry["version"] == "1.2.3"
    assert entry["compatible"] is True
    assert entry["ref"] == "main"


def test_direct_github_install_is_tracked_as_direct(tmp_path, monkeypatch):
    store, plugins = build_store(tmp_path, monkeypatch)
    entry = {
        "id": "community_demo",
        "name": "Community Demo",
        "description": "Test plugin",
        "version": "1.2.3",
        "repository": "https://github.com/example/community-demo",
        "ref": "main",
        "ref_kind": "head",
        "compatible": True,
        "compatibility_reason": None,
        "min_host": None,
    }
    monkeypatch.setattr(store, "inspect_github_plugin", lambda repository: entry)

    archive_template = tmp_path / "direct.zip"
    with zipfile.ZipFile(archive_template, "w") as zf:
        zf.writestr(
            "community-demo-main/plugin.json",
            json.dumps({
                "id": "community_demo",
                "name": "Community Demo",
                "version": "1.2.3",
            }),
        )
        zf.writestr("community-demo-main/screen.js", "// demo")

    monkeypatch.setattr(
        store,
        "_download",
        lambda entry, destination, **kwargs: destination.write_bytes(archive_template.read_bytes()),
    )

    result = store.install_from_github(
        "https://github.com/example/community-demo",
        acknowledge_third_party=True,
    )
    assert result["ok"] is True
    assert (plugins / "community_demo" / "plugin.json").is_file()

    managed = store._managed()["community_demo"]
    assert managed["store_id"] == storelib.DIRECT_STORE_ID
    assert managed["direct"] is True
    assert managed["tracking_ref"] == "main"


def test_versions_list_valid_tags_only(tmp_path, monkeypatch):
    store, _plugins = build_store(tmp_path, monkeypatch)

    base = {
        "id": "demo",
        "name": "Demo",
        "description": "",
        "version": "2.0.0",
        "repository": "https://github.com/example/demo",
        "ref": "main",
        "ref_kind": "head",
    }
    monkeypatch.setattr(
        store,
        "_entry",
        lambda plugin_id, store_id="official": (base, {"id": store_id, "name": "Test"}, False),
    )
    monkeypatch.setattr(store, "_github_tags", lambda repository: ["v2.0.0", "v1.5.0", "bad"])
    manifests = {
        "v2.0.0": {"id": "demo", "name": "Demo", "version": "2.0.0"},
        "v1.5.0": {"id": "demo", "name": "Demo", "version": "1.5.0"},
        "bad": {"id": "different", "name": "Wrong", "version": "9.0.0"},
    }
    monkeypatch.setattr(
        store,
        "_fetch_repo_manifest",
        lambda repository, ref, plugin_id_hint="plugin": manifests[ref],
    )

    result = store.versions("official", "demo")
    values = {(item["version"], item["ref_kind"]) for item in result["versions"]}
    assert ("2.0.0", "head") in values
    assert ("2.0.0", "tag") in values
    assert ("1.5.0", "tag") in values
    assert all(item["version"] != "9.0.0" for item in result["versions"])


def test_v040_routes_and_ui_surface():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    html = (ROOT / "screen.html").read_text(encoding="utf-8")
    js = (ROOT / "screen.js").read_text(encoding="utf-8")

    assert '@app.post(f"{API_PREFIX}/direct/install")' in routes
    assert '@app.get(f"{API_PREFIX}/check/{{store_id}}/{{plugin_id}}")' in routes
    assert '@app.post(f"{API_PREFIX}/exclude/{{plugin_id}}")' in routes
    assert '@app.get(f"{API_PREFIX}/versions/{{store_id}}/{{plugin_id}}")' in routes
    assert '@app.post(f"{API_PREFIX}/version/{{store_id}}/{{plugin_id}}")' in routes

    assert 'id="plugin-store-install-github"' in html
    assert 'id="plugin-store-versions-dialog"' in html
    assert "function checkPlugin(" in js
    assert "function toggleExcluded(" in js
    assert "function showVersions(" in js
    assert "function submitGithubInstall(" in js
