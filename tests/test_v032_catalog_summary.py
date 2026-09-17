import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_v032():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.3"


def test_catalog_summary_markup_exists():
    html = (ROOT / "screen.html").read_text(encoding="utf-8")
    assert 'id="plugin-store-summary"' in html
    assert 'id="plugin-store-summary-installed"' in html
    assert 'id="plugin-store-summary-updates"' in html
    assert 'id="plugin-store-summary-available"' in html


def test_catalog_summary_logic_exists():
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert "function catalogSummary(" in js
    assert "function renderCatalogSummary(" in js
    assert "renderCatalogSummary(data);" in js
    assert 'plugin.status === "update_available"' in js
