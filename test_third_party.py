import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("plugin_store_storelib_tp", ROOT / "storelib.py")
storelib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = storelib
SPEC.loader.exec_module(storelib)


class DummyLog:
    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def test_github_blob_store_url_is_normalized_to_raw():
    url = "https://github.com/example/store/blob/main/community.yaml"
    assert storelib.normalize_registry_url(url) == (
        "https://raw.githubusercontent.com/example/store/main/community.yaml"
    )


def test_non_github_store_url_is_rejected():
    with pytest.raises(storelib.StoreError):
        storelib.normalize_registry_url("https://192.168.1.10/plugins.yaml")
    with pytest.raises(storelib.StoreError):
        storelib.normalize_registry_url("http://example.com/plugins.yaml")


def test_third_party_registry_requires_store_metadata():
    with pytest.raises(storelib.StoreError):
        storelib.load_registry_yaml(
            """
schema: 1
plugins: []
""",
            official=False,
        )


def test_third_party_registry_allows_non_official_github_owner():
    data = storelib.load_registry_yaml(
        """
schema: 1
store:
  name: Community Store
  homepage: https://github.com/example/community-store
plugins:
  - id: community_tuner
    name: Community Tuner
    description: Example
    version: 1.0.0
    repository: https://github.com/example/community-tuner
    ref: main
""",
        official=False,
    )
    assert data["store"]["name"] == "Community Store"
    assert data["plugins"][0]["id"] == "community_tuner"


def test_manifest_min_host_blocks_old_feedback():
    manifest = {
        "id": "community_tuner",
        "name": "Community Tuner",
        "version": "1.0.0",
        "minHost": "0.5.0",
    }
    result = storelib.validate_plugin_manifest(manifest, host_version="0.4.9")
    assert result["compatible"] is False
    assert "0.5.0" in result["compatibility_reason"]


def test_manifest_min_host_accepts_newer_feedback():
    manifest = {
        "id": "community_tuner",
        "name": "Community Tuner",
        "version": "1.0.0",
        "minHost": "0.5.0",
    }
    result = storelib.validate_plugin_manifest(manifest, host_version="0.5.1")
    assert result["compatible"] is True


def test_manifest_referenced_files_are_checked(tmp_path):
    manifest = {
        "id": "community_tuner",
        "version": "1.0.0",
        "screen": "screen.html",
    }
    with pytest.raises(storelib.StoreError, match="missing screen"):
        storelib.validate_plugin_manifest(manifest, plugin_root=tmp_path)


def test_add_store_validates_yaml_and_remote_plugin_manifest(tmp_path, monkeypatch):
    config = tmp_path / "config"
    plugins = tmp_path / "plugins"
    plugin_dir = tmp_path / "plugin_store"
    plugin_dir.mkdir()
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugins))
    monkeypatch.setenv("FEEDBACK_HOST_VERSION", "0.9.0")

    store = storelib.PluginStore(config, plugin_dir, DummyLog())

    registry = """
schema: 1
store:
  name: Example Community
  description: Community plugins
plugins:
  - id: example_plugin
    name: Example Plugin
    description: Example
    version: 1.2.3
    repository: https://github.com/example/example-plugin
    ref: main
"""
    manifest = json.dumps({
        "id": "example_plugin",
        "name": "Example Plugin",
        "version": "1.2.3",
        "minHost": "0.8.0",
    })

    def fake_fetch(url, *, max_bytes, timeout, headers=None):
        if url.endswith("plugin.json"):
            return manifest, {"etag": "m"}, url
        return registry, {"etag": "r"}, url

    monkeypatch.setattr(store, "_fetch_text", fake_fetch)
    monkeypatch.setattr(store, "_official_ids", lambda: set())

    result = store.add_store(
        "https://raw.githubusercontent.com/example/store/main/store.yaml",
        acknowledge_risk=True,
    )
    assert result["ok"] is True
    assert result["store"]["name"] == "Example Community"
    saved = json.loads(store.stores_path.read_text(encoding="utf-8"))
    assert len(saved["stores"]) == 1


def test_add_store_rejects_registry_manifest_mismatch(tmp_path, monkeypatch):
    config = tmp_path / "config"
    plugins = tmp_path / "plugins"
    plugin_dir = tmp_path / "plugin_store"
    plugin_dir.mkdir()
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugins))
    monkeypatch.setenv("FEEDBACK_HOST_VERSION", "0.9.0")
    store = storelib.PluginStore(config, plugin_dir, DummyLog())

    registry = """
schema: 1
store:
  name: Example Community
plugins:
  - id: expected_id
    name: Example
    description: Example
    version: 1.0.0
    repository: https://github.com/example/example-plugin
    ref: main
"""
    bad_manifest = json.dumps({"id": "different_id", "version": "1.0.0"})

    def fake_fetch(url, *, max_bytes, timeout, headers=None):
        if url.endswith("plugin.json"):
            return bad_manifest, {}, url
        return registry, {}, url

    monkeypatch.setattr(store, "_fetch_text", fake_fetch)
    monkeypatch.setattr(store, "_official_ids", lambda: set())

    with pytest.raises(storelib.StoreError, match="does not match"):
        store.add_store(
            "https://raw.githubusercontent.com/example/store/main/store.yaml",
            acknowledge_risk=True,
        )


def test_third_party_store_cannot_claim_official_plugin_id(tmp_path, monkeypatch):
    config = tmp_path / "config"
    plugins = tmp_path / "plugins"
    plugin_dir = tmp_path / "plugin_store"
    plugin_dir.mkdir()
    (plugin_dir / "registry.yaml").write_text("schema: 1\nplugins: []\n", encoding="utf-8")
    monkeypatch.setenv("FEEDBACK_PLUGINS_DIR", str(plugins))
    store = storelib.PluginStore(config, plugin_dir, DummyLog())

    registry = """
schema: 1
store:
  name: Evil-ish Store
plugins:
  - id: note_detect
    name: Fake Note Detect
    description: Spoof
    version: 1.0.0
    repository: https://github.com/example/fake-note-detect
    ref: main
"""

    def fake_fetch(url, *, max_bytes, timeout, headers=None):
        return registry, {}, url

    monkeypatch.setattr(store, "_fetch_text", fake_fetch)
    monkeypatch.setattr(store, "_official_ids", lambda: {"note_detect"})

    with pytest.raises(storelib.StoreError, match="reserved official"):
        store.add_store(
            "https://raw.githubusercontent.com/example/store/main/store.yaml",
            acknowledge_risk=True,
        )
