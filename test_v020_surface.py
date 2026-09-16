from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_routes_expose_third_party_store_management():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    assert '@app.post(f"{API_PREFIX}/stores")' in routes
    assert '@app.delete(f"{API_PREFIX}/stores/{store_id}")' not in routes  # braces are escaped in source
    assert '@app.delete(f"{API_PREFIX}/stores/{{store_id}}")' in routes
    assert '@app.post(f"{API_PREFIX}/install/{{store_id}}/{{plugin_id}}")' in routes
    assert "acknowledge_third_party" in routes


def test_ui_has_third_party_warning_and_add_store_flow():
    html = (ROOT / "screen.html").read_text(encoding="utf-8")
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert "Add Third-Party Store" in html
    assert "malicious" in html.lower()
    assert "acknowledge_risk" in js
    assert "THIRD-PARTY STORE" in js
    assert "THIRD-PARTY" in js
    assert "thirdPartyInstallWarning" in js


def test_sidebar_nav_manifest_is_present():
    manifest = (ROOT / "plugin.json").read_text(encoding="utf-8")
    assert '"label": "Plugin Store"' in manifest
    assert '"screen": "plugin-plugin_store"' in manifest


def test_documented_community_store_example_exists():
    assert (ROOT / "docs" / "third-party-stores.md").is_file()
    assert (ROOT / "examples" / "community-store.yaml").is_file()
