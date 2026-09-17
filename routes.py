"""FastAPI routes for the feedBack Plugin Store.

Importing this module performs no filesystem or network I/O, per the feedBack
plugin specification. All setup happens inside setup().
"""

from __future__ import annotations

import os
import platform
import signal
import threading
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query


PLUGIN_ID = "plugin_store"
API_PREFIX = f"/api/plugins/{PLUGIN_ID}"



RESTART_MODE_ENV = "FEEDBACK_PLUGIN_STORE_RESTART_MODE"
VALID_RESTART_MODES = {"auto", "container", "desktop", "manual"}


def _read_linux_cgroup() -> str:
    chunks: list[str] = []
    for candidate in ("/proc/1/cgroup", "/proc/self/cgroup"):
        try:
            chunks.append(Path(candidate).read_text(encoding="utf-8", errors="ignore"))
        except OSError:
            pass
    return "\n".join(chunks).lower()


def _detect_restart_mode(
    *,
    environ: dict[str, str] | None = None,
    system_name: str | None = None,
    dockerenv_exists: bool | None = None,
    cgroup_text: str | None = None,
) -> dict:
    """Return the safest restart strategy we can infer.

    `container` and `desktop` both use process termination, but for different
    supervisors: Docker/systemd-style container supervision vs the native
    feedBack Desktop shell. `manual` never terminates the process.
    """
    env = dict(os.environ if environ is None else environ)
    requested = str(env.get(RESTART_MODE_ENV, "auto") or "auto").strip().lower()
    if requested not in VALID_RESTART_MODES:
        requested = "auto"

    system = system_name or platform.system() or "Unknown"

    if requested != "auto":
        automatic = requested in {"container", "desktop"}
        return {
            "mode": requested,
            "automatic": automatic,
            "platform": system,
            "reason": f"Explicit {RESTART_MODE_ENV} override.",
        }

    if dockerenv_exists is None:
        dockerenv_exists = Path("/.dockerenv").exists()
    if cgroup_text is None:
        cgroup_text = _read_linux_cgroup()

    container_tokens = ("docker", "containerd", "kubepods", "podman", "lxc")
    container_env = str(env.get("container", "")).strip().lower()

    if (
        dockerenv_exists
        or any(token in cgroup_text for token in container_tokens)
        or container_env in {"docker", "podman", "lxc", "containerd"}
    ):
        return {
            "mode": "container",
            "automatic": True,
            "platform": system,
            "reason": "Container runtime detected.",
        }

    # A native Windows/macOS backend is expected to be owned by feedBack
    # Desktop. Linux Desktop ships as an AppImage, which normally propagates
    # APPIMAGE/APPDIR to child processes.
    if system in {"Windows", "Darwin"}:
        return {
            "mode": "desktop",
            "automatic": True,
            "platform": system,
            "reason": f"Native {system} host detected.",
        }

    linux_desktop_hints = (
        "APPIMAGE",
        "APPDIR",
        "FEEDBACK_DESKTOP",
        "FEEDBACK_DESKTOP_APP",
        "SLOPSMITH_DESKTOP",
    )
    if system == "Linux" and any(str(env.get(key, "")).strip() for key in linux_desktop_hints):
        return {
            "mode": "desktop",
            "automatic": True,
            "platform": system,
            "reason": "Linux desktop/AppImage environment detected.",
        }

    return {
        "mode": "manual",
        "automatic": False,
        "platform": system,
        "reason": "No container or native desktop supervisor could be identified safely.",
    }



def setup(app: FastAPI, context: dict) -> None:
    config_dir = Path(context["config_dir"])
    log = context["log"]
    load_sibling = context["load_sibling"]

    storelib = load_sibling("storelib")
    try:
        store = storelib.PluginStore(
            config_dir=config_dir,
            plugin_dir=Path(__file__).resolve().parent,
            log=log,
        )
    except storelib.StoreError as exc:
        log.error("plugin_store_setup_failed", extra={"error": exc.message})
        raise RuntimeError(exc.message) from exc

    def fail(exc):
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    def require_mutation_header(value: str | None) -> None:
        # This non-simple header forces cross-origin browser requests through a
        # CORS preflight instead of allowing a plain cross-site HTML form to
        # mutate plugin/store state.
        if value != "1":
            raise HTTPException(
                status_code=403,
                detail="Missing Plugin Store request header.",
            )

    instance_id = uuid.uuid4().hex

    def restart_info() -> dict:
        return _detect_restart_mode()

    @app.get(f"{API_PREFIX}/instance")
    def get_instance():
        return {
            "instance_id": instance_id,
            "restart": restart_info(),
        }

    @app.get(f"{API_PREFIX}/restart-info")
    def get_restart_info():
        return restart_info()

    def schedule_restart() -> dict:
        info = restart_info()
        if not info["automatic"]:
            return {
                **info,
                "restarting": False,
                "manual_required": True,
            }

        def terminate_process():
            try:
                log.info(
                    "plugin_store_restart_requested",
                    extra={
                        "pid": os.getpid(),
                        "instance_id": instance_id,
                        "restart_mode": info["mode"],
                        "platform_name": info["platform"],
                    },
                )
            except Exception:
                # Restart must not depend on a particular logging adapter.
                pass

            # Container mode relies on the configured container supervisor.
            # Desktop mode relies on feedBack Desktop supervising its backend.
            os.kill(os.getpid(), signal.SIGTERM)

        # Let the HTTP response reach the frontend before terminating backend.
        timer = threading.Timer(1.0, terminate_process)
        timer.daemon = True
        timer.start()
        return {
            **info,
            "restarting": True,
            "manual_required": False,
        }

    @app.post(f"{API_PREFIX}/restart")
    def restart_feedback(
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        restart = schedule_restart()
        return {
            "ok": True,
            "instance_id": instance_id,
            **restart,
        }

    @app.get(f"{API_PREFIX}/self-update")
    def get_self_update(refresh: bool = Query(default=False)):
        try:
            return store.self_update_status(force=refresh)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/self-update/install")
    def install_self_update(
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            result = store.install_self_update()
        except storelib.StoreError as exc:
            fail(exc)
        restart = schedule_restart()
        return {
            **result,
            "instance_id": instance_id,
            **restart,
        }

    @app.get(f"{API_PREFIX}/catalog")
    def get_catalog(refresh: bool = Query(default=False)):
        try:
            return store.catalog(force_refresh=refresh)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/stores")
    def add_store(
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.add_store(
                str(payload.get("url", "")),
                acknowledge_risk=payload.get("acknowledge_risk") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.delete(f"{API_PREFIX}/stores/{{store_id}}")
    def remove_store(
        store_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.remove_store(store_id)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/direct/install")
    def install_direct_github_plugin(
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install_from_github(
                str(payload.get("repository", "")),
                acknowledge_third_party=payload.get("acknowledge_third_party") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.get(f"{API_PREFIX}/check/{{store_id}}/{{plugin_id}}")
    def check_plugin(
        store_id: str,
        plugin_id: str,
    ):
        try:
            return store.check_plugin(store_id, plugin_id)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/exclude/{{plugin_id}}")
    def set_plugin_excluded(
        plugin_id: str,
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.set_excluded(
                plugin_id,
                payload.get("excluded") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.get(f"{API_PREFIX}/versions/{{store_id}}/{{plugin_id}}")
    def get_plugin_versions(
        store_id: str,
        plugin_id: str,
    ):
        try:
            return store.versions(store_id, plugin_id)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/version/{{store_id}}/{{plugin_id}}")
    def install_plugin_version(
        store_id: str,
        plugin_id: str,
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install_version(
                store_id,
                plugin_id,
                str(payload.get("ref", "")),
                str(payload.get("ref_kind", "")),
                acknowledge_third_party=payload.get("acknowledge_third_party") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/update-all")
    def update_all_plugins(
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.update_all(
                acknowledge_third_party=payload.get("acknowledge_third_party") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/rollback/{{plugin_id}}")
    def rollback_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.rollback(plugin_id)
        except storelib.StoreError as exc:
            fail(exc)

    # v0.2 routes include an explicit store id so two catalogs can never be
    # ambiguous about which repository is being installed.
    @app.post(f"{API_PREFIX}/install/{{store_id}}/{{plugin_id}}")
    def install_plugin_from_store(
        store_id: str,
        plugin_id: str,
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install(
                plugin_id,
                replace=False,
                store_id=store_id,
                acknowledge_third_party=payload.get("acknowledge_third_party") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/update/{{store_id}}/{{plugin_id}}")
    def update_plugin_from_store(
        store_id: str,
        plugin_id: str,
        payload: dict = Body(default={}),
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install(
                plugin_id,
                replace=True,
                store_id=store_id,
                acknowledge_third_party=payload.get("acknowledge_third_party") is True,
            )
        except storelib.StoreError as exc:
            fail(exc)

    @app.delete(f"{API_PREFIX}/remove/{{store_id}}/{{plugin_id}}")
    def remove_plugin_from_store(
        store_id: str,
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.remove(plugin_id, store_id=store_id)
        except storelib.StoreError as exc:
            fail(exc)

    # Keep the original v0.1 API working for callers that only know about the
    # official registry.
    @app.post(f"{API_PREFIX}/install/{{plugin_id}}")
    def install_official_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install(plugin_id, replace=False)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/update/{{plugin_id}}")
    def update_official_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install(plugin_id, replace=True)
        except storelib.StoreError as exc:
            fail(exc)

    @app.delete(f"{API_PREFIX}/remove/{{plugin_id}}")
    def remove_official_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.remove(plugin_id)
        except storelib.StoreError as exc:
            fail(exc)
