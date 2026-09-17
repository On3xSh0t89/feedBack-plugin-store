import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_v033():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.1"


def test_self_update_refreshes_exact_frontend_asset_urls():
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert "async function refreshPluginStoreFrontendAssets(" in js
    assert '"/api/plugins/plugin_store/screen.html"' in js
    assert '"/api/plugins/plugin_store/screen.js"' in js
    assert '"/api/plugins/plugin_store/settings.html"' in js
    assert '"/api/plugins/plugin_store/assets/plugin.css"' in js
    assert 'cache: "reload"' in js
    assert '"Cache-Control": "no-cache"' in js


def test_self_update_waits_for_expected_backend_version():
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert 'fetch(`/api/plugins?_=${Date.now()}`' in js
    assert 'plugin.id === "plugin_store"' in js
    assert "self.version === expectedVersion" in js


def test_self_update_hard_navigates_after_asset_refresh():
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    refresh = js.index("await refreshPluginStoreFrontendAssets(result.version);")
    reload = js.index("hardReloadAfterSelfUpdate(result.version);")
    assert refresh < reload
    assert 'url.searchParams.set(' in js
    assert '"_plugin_store_reload"' in js
    assert "window.location.replace(url.toString())" in js
