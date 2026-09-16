from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_registry_has_current_official_plugins():
    data = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    assert data["schema"] == 1
    assert len(data["plugins"]) == 37
    ids = {item["id"] for item in data["plugins"]}
    assert "note_detect" in ids
    assert "rig_builder" in ids
    assert "musicxml_import" in ids
    assert "piano" in ids
    assert "drums" in ids
    assert "midi_amp" in ids
    assert "feedback-plugin-bongocat" not in ids


def test_registry_repos_are_official_org():
    data = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    for item in data["plugins"]:
        assert item["repository"].lower().startswith("https://github.com/got-feedback/")
