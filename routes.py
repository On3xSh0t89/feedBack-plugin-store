"""FastAPI routes for the feedBack Plugin Store.

Importing this module performs no filesystem or network I/O, per the feedBack
plugin specification. All setup happens inside setup().
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query


PLUGIN_ID = "plugin_store"
API_PREFIX = f"/api/plugins/{PLUGIN_ID}"


def setup(app: FastAPI, context: dict) -> None:
    config_dir = Path(context["config_dir"])
    log = context["log"]
    load_sibling = context["load_sibling"]

    # Validate everything before mounting the first route.
    storelib = load_sibling("storelib")
    try:
        store = storelib.PluginStore(
            config_dir=config_dir,
            plugin_dir=Path(__file__).resolve().parent,
            log=log,
        )
    except storelib.StoreError as exc:
        # Do not partially register a broken management surface.
        log.error(
            "plugin_store_setup_failed",
            extra={"error": exc.message},
        )
        raise RuntimeError(exc.message) from exc

    def fail(exc):
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    def require_mutation_header(value: str | None) -> None:
        # This custom header forces cross-origin browser requests through CORS
        # preflight instead of allowing a plain HTML form to mutate plugin state.
        if value != "1":
            raise HTTPException(status_code=403, detail="Missing Plugin Store request header.")

    @app.get(f"{API_PREFIX}/catalog")
    def get_catalog(refresh: bool = Query(default=False)):
        try:
            return store.catalog(force_refresh=refresh)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/install/{{plugin_id}}")
    def install_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install(plugin_id, replace=False)
        except storelib.StoreError as exc:
            fail(exc)

    @app.post(f"{API_PREFIX}/update/{{plugin_id}}")
    def update_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.install(plugin_id, replace=True)
        except storelib.StoreError as exc:
            fail(exc)

    @app.delete(f"{API_PREFIX}/remove/{{plugin_id}}")
    def remove_plugin(
        plugin_id: str,
        x_feedback_plugin_store: str | None = Header(default=None),
    ):
        require_mutation_header(x_feedback_plugin_store)
        try:
            return store.remove(plugin_id)
        except storelib.StoreError as exc:
            fail(exc)
