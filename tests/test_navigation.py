from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_exposes_installed_plugin_navigation_metadata():
    src = (ROOT / "storelib.py").read_text(encoding="utf-8")
    assert '"has_settings": False' in src
    assert '"has_screen": False' in src
    assert '"nav_screen": None' in src
    assert 'settings.get("html")' in src
    assert 'nav.get("screen")' in src


def test_frontend_uses_native_feedback_navigation():
    src = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert 'window.showScreen("settings")' in src
    assert '"#plugin-settings details[data-plugin-id]"' in src
    assert 'window.showScreen(screenId)' in src
    assert '"Settings"' in src
    assert '"Open Plugin"' in src


def test_installed_card_prefers_settings_when_clicked():
    src = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert "function openInstalledPlugin(plugin)" in src
    settings_pos = src.index("if (plugin.has_settings)", src.index("function openInstalledPlugin"))
    screen_pos = src.index("else if (plugin.has_screen)", settings_pos)
    assert settings_pos < screen_pos
