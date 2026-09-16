from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_restart_route_uses_sigterm_without_docker_socket():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    assert '@app.post(f"{API_PREFIX}/restart")' in routes
    assert "signal.SIGTERM" in routes
    assert "os.kill(os.getpid()" in routes
    assert "/var/run/docker.sock" not in routes
    assert "docker restart" not in routes.lower()


def test_instance_endpoint_exists_for_restart_detection():
    routes = (ROOT / "routes.py").read_text(encoding="utf-8")
    screen = (ROOT / "screen.js").read_text(encoding="utf-8")
    assert '@app.get(f"{API_PREFIX}/instance")' in routes
    assert 'api("/restart", { method: "POST" })' in screen
    assert "waitForNewInstance" in screen
    assert "window.location.reload()" in screen
