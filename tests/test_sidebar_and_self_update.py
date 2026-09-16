from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sidebar_entry_is_injected_after_plugins():
    src = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert "function ensureSidebarEntry()" in src
    assert '[data-v3-nav="plugins"]' in src
    assert 'pluginsEntry.insertAdjacentElement("afterend", entry)' in src
    assert 'window.showScreen("plugin-plugin_store")' in src


def test_sidebar_entry_is_readded_if_shell_rerenders():
    src = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert "new MutationObserver" in src
    assert "sidebarObserver.observe" in src


def test_self_update_endpoint_and_ui_exist():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    storelib = (ROOT / "storelib.py").read_text(encoding="utf-8")
    screen = (ROOT / "screen.js").read_text(encoding="utf-8")
    html = (ROOT / "screen.html").read_text(encoding="utf-8")

    assert '@app.get(f"{API_PREFIX}/self-update")' in routes
    assert "def self_update_status(" in storelib
    assert 'id="plugin-store-update-banner"' in html
    assert "function checkSelfUpdate(" in screen
    assert "update_available" in screen


def test_self_update_reads_only_remote_manifest():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    assert "raw.githubusercontent.com" in src
    assert "self-update.json" in src
    assert "/var/run/docker.sock" not in src
