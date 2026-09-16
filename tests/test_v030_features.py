import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_current_release():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.3.2"


def test_backend_has_bounded_rollback_snapshots():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    assert "DEFAULT_BACKUPS_PER_PLUGIN = 2" in src
    assert "FEEDBACK_PLUGIN_STORE_BACKUPS_PER_PLUGIN" in src
    assert "def _snapshot_backup(" in src
    assert "def rollback(" in src
    assert '"rollback_available": False' in src
    assert "self._trim_backups(plugin_id)" in src


def test_updates_snapshot_before_replacement():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    snapshot_pos = src.index("rollback_snapshot = self._snapshot_backup(")
    replace_pos = src.index("os.replace(target, backup)", snapshot_pos)
    assert snapshot_pos < replace_pos


def test_update_all_backend_exists_and_requires_third_party_ack():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    assert "def update_all(" in src
    assert "Update All includes third-party plugins" in src
    assert '@app.post(f"{API_PREFIX}/update-all")' in routes


def test_rollback_route_exists():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    assert '@app.post(f"{API_PREFIX}/rollback/{{plugin_id}}")' in routes


def test_search_filters_update_all_and_rollback_ui_exist():
    html = (ROOT / "screen.html").read_text(encoding="utf-8")
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert 'id="plugin-store-search"' in html
    assert 'data-filter="installed"' in html
    assert 'data-filter="updates"' in html
    assert 'id="plugin-store-update-all"' in html
    assert "function pluginMatchesView(" in js
    assert "function updateAllPlugins(" in js
    assert "function rollbackPlugin(" in js
    assert "Roll Back to" in js
