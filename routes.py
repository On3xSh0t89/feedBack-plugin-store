"""FastAPI routes for the feedBack Plugin Store.

Importing this module performs no filesystem or network I/O, per the feedBack
plugin specification. All setup happens inside setup().
"""

from __future__ import annotations

import os
import signal
import threading
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query


PLUGIN_ID = "plugin_store"
API_PREFIX = f"/api/plugins/{PLUGIN_ID}"


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

    @app.get(f"{API_PREFIX}/instance")
    def get_instance():
        return {"instance_id": instance_id}

    @app.post(f"{API_PREFIX}/restart")
    def restart_feedback(
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)

        def terminate_process():
            log.info(
                "plugin_store_restart_requested",
                extra={"pid": os.getpid(), "instance_id": instance_id},
            )
            os.kill(os.getpid(), signal.SIGTERM)

        # Let the HTTP response reach the browser before terminating feedBack.
        # Docker's restart policy/supervisor is responsible for bringing it back.
        timer = threading.Timer(1.0, terminate_process)
        timer.daemon = True
        timer.start()
        return {"ok": True, "restarting": True, "instance_id": instance_id}

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
