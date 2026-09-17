import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location(
    "plugin_store_v042_routes",
    ROOT / "routes.py",
)
routes = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = routes
SPEC.loader.exec_module(routes)


def test_manifest_is_v042():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.4.2"


def test_docker_detection_wins_in_auto_mode():
    info = routes._detect_restart_mode(
        environ={},
        system_name="Linux",
        dockerenv_exists=True,
        cgroup_text="",
    )
    assert info["mode"] == "container"
    assert info["automatic"] is True


def test_container_cgroup_is_detected():
    info = routes._detect_restart_mode(
        environ={},
        system_name="Linux",
        dockerenv_exists=False,
        cgroup_text="0::/docker/abcdef",
    )
    assert info["mode"] == "container"


def test_windows_native_is_desktop():
    info = routes._detect_restart_mode(
        environ={},
        system_name="Windows",
        dockerenv_exists=False,
        cgroup_text="",
    )
    assert info["mode"] == "desktop"
    assert info["automatic"] is True


def test_macos_native_is_desktop():
    info = routes._detect_restart_mode(
        environ={},
        system_name="Darwin",
        dockerenv_exists=False,
        cgroup_text="",
    )
    assert info["mode"] == "desktop"
    assert info["automatic"] is True


def test_linux_appimage_is_desktop():
    info = routes._detect_restart_mode(
        environ={"APPIMAGE": "/home/user/feedBack.AppImage"},
        system_name="Linux",
        dockerenv_exists=False,
        cgroup_text="",
    )
    assert info["mode"] == "desktop"
    assert info["automatic"] is True


def test_unknown_linux_falls_back_to_manual():
    info = routes._detect_restart_mode(
        environ={},
        system_name="Linux",
        dockerenv_exists=False,
        cgroup_text="",
    )
    assert info["mode"] == "manual"
    assert info["automatic"] is False


def test_manual_override_disables_automatic_restart():
    info = routes._detect_restart_mode(
        environ={"FEEDBACK_PLUGIN_STORE_RESTART_MODE": "manual"},
        system_name="Windows",
        dockerenv_exists=True,
        cgroup_text="docker",
    )
    assert info["mode"] == "manual"
    assert info["automatic"] is False


def test_explicit_desktop_override_is_supported():
    info = routes._detect_restart_mode(
        environ={"FEEDBACK_PLUGIN_STORE_RESTART_MODE": "desktop"},
        system_name="Linux",
        dockerenv_exists=True,
        cgroup_text="docker",
    )
    assert info["mode"] == "desktop"
    assert info["automatic"] is True


def test_restart_info_route_and_manual_ui_exist():
    routes_src = (ROOT / "routes.py").read_text(encoding="utf-8")
    js = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert '@app.get(f"{API_PREFIX}/restart-info")' in routes_src
    assert "manual_required" in routes_src
    assert 'api("/restart-info")' in js
    assert "restartFailureMessage" in js
    assert "Quit feedBack completely and reopen it." in js
