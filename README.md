# feedBack Plugin Store

A plugin manager for [feedBack](https://github.com/got-feedBack/feedBack), maintained at:

**https://github.com/irnutsmurt/feedBack-plugin-store**

## What it does

- Presents the official `got-feedBack` plugin catalog inside feedBack.
- Adds a **Plugin Store** entry to feedBack's plugin navigation/sidebar.
- Installs, updates, and removes plugins without requiring `git` inside the feedBack container.
- Deep-links installed plugins to their native **Settings** panel and/or plugin screen.
- Offers an in-app feedBack restart after a plugin lifecycle change.
- Supports optional, clearly-labelled **third-party stores** hosted as YAML on GitHub.
- Uses conditional HTTP requests (`ETag` / `If-None-Match`) and local cache files so unchanged catalogs are not repeatedly downloaded.

The bundled official registry is generated from the public repositories under the `got-feedBack` organization and each plugin repository's actual `plugin.json`. Repositories marked `private: true`, forks, and non-runtime repositories such as `feedBack-plugin-spec` are not offered.

## Recommended Docker layout

```yaml
services:
  feedback:
    image: ghcr.io/got-feedback/feedback:nightly
    container_name: feedback

    ports:
      - "48000:8000"

    volumes:
      - "/volume2/General Storage/feedback/dlc:/dlc"
      - "/volume1/docker/feedback:/config"
      - "/volume1/docker/feedback/user-plugins:/user-plugins"

    environment:
      DLC_DIR: /dlc
      CONFIG_DIR: /config
      FEEDBACK_PLUGINS_DIR: /user-plugins

    restart: unless-stopped
```

## Install the store

```bash
mkdir -p "/volume1/docker/feedback/user-plugins"
cd "/volume1/docker/feedback/user-plugins"

git clone https://github.com/irnutsmurt/feedBack-plugin-store.git plugin_store
```

Then restart feedBack once:

```bash
docker compose restart
```

The clone directory must be named `plugin_store` because feedBack requires a plugin directory name to equal its manifest `id`.

## Official registry updates

When installed with `git clone`, Plugin Store derives the raw `registry.yaml` URL from its own Git origin. ZIP installs fall back to the `homepage` in `plugin.json`. The store caches GitHub's `ETag` and sends `If-None-Match`; an unchanged registry produces `304 Not Modified` and the cached copy is reused.

To regenerate the official registry from the feedBack organization:

```bash
python tools/sync_registry.py
```

An optional `GITHUB_TOKEN` can be supplied to avoid anonymous GitHub API rate limits.

## Third-party stores

Click **Add Third-Party Store** in Plugin Store and paste the URL of a GitHub-hosted YAML catalog. A normal GitHub `blob/.../store.yaml` link is accepted and converted to its raw URL automatically.

Third-party catalogs are visually separated from the official catalog and every plugin is marked **THIRD-PARTY**.

Adding a store requires an explicit security acknowledgement. Installing or updating a plugin from that store requires a second acknowledgement because feedBack plugins can execute server-side Python and browser JavaScript with feedBack's permissions.

Removing a third-party store removes only the catalog source. Plugins already installed from it are preserved.

### Third-party store format

```yaml
schema: 1

store:
  name: Example Community Store
  description: Optional description of this plugin catalog.
  homepage: https://github.com/example/community-store

plugins:
  - id: example_plugin
    name: Example Plugin
    description: Does an example thing in feedBack.
    version: 1.2.3
    repository: https://github.com/example/feedBack-plugin-example
    ref: main
```

`repository` must be an HTTPS GitHub repository. `ref` is currently treated as a branch name.

For more detail, see [`docs/third-party-stores.md`](docs/third-party-stores.md).

## Compatibility gates

Plugin Store fails closed when it cannot establish the compatibility information it needs.

When a third-party store is added or its YAML changes, Plugin Store checks:

- catalog `schema: 1` and required `store.name` metadata;
- valid plugin ids and semantic versions;
- HTTPS GitHub repository URLs;
- duplicate plugin ids;
- attempts to claim an id already reserved by the official feedBack catalog;
- the root `plugin.json` for every listed repository/ref;
- registry id/version against the actual `plugin.json` id/version;
- `private: true` manifests (rejected);
- feedBack `minHost` compatibility when declared.

Immediately before installation, the downloaded archive is checked again. Plugin Store also validates the stable feedBack v1 manifest contract it relies on and verifies that manifest-referenced files such as `screen`, `script`, `routes`, `styles`, `tour`, and `settings.html` actually exist inside the extracted plugin.

The current feedBack plugin spec identifies **specification 1.0.0 / manifest major 1**. Unknown manifest keys remain allowed because the feedBack manifest is forward-extensible.

If a plugin declares `minHost` and the running feedBack version cannot be verified, that third-party plugin is disabled rather than guessed compatible. Docker builds expose the Host version at `/app/VERSION`; `FEEDBACK_HOST_VERSION` can be used as an explicit override for unusual deployments.

## Third-party security model

Third-party registries are untrusted input. v0.2 deliberately narrows the remote-fetch surface:

- store YAML must be HTTPS and hosted on `raw.githubusercontent.com` or GitHub Pages (`*.github.io`);
- pasted `github.com/.../blob/...` YAML links are normalized to `raw.githubusercontent.com`;
- arbitrary LAN URLs, IP addresses, `localhost`, HTTP, `file://`, and arbitrary hosts are not fetched;
- third-party plugin repositories must be HTTPS `github.com` repositories;
- a third-party store cannot override an official plugin id;
- two configured third-party stores cannot claim the same plugin id;
- third-party installs/updates require explicit acknowledgement in both UI and backend;
- an installed third-party plugin can be automatically updated/removed only by the store that originally installed it;
- adding/removing a store never automatically installs, updates, or deletes a plugin.

This does **not** make third-party plugin code safe. It provides provenance, validation, collision protection, and an explicit trust boundary. Users still need to trust the code they install.

## In-app restart

After an install, update, or removal, Plugin Store shows **Restart feedBack**.

The restart endpoint does not require the Docker socket. It returns success to the browser, waits one second, then sends `SIGTERM` to the running feedBack Python process. The browser polls a per-process instance id and reloads only after a new feedBack process is serving requests.

For Docker this requires a restart policy such as:

```yaml
restart: unless-stopped
```

If feedBack is run without Docker or another supervisor/restart policy, using the restart button will stop feedBack and it will remain stopped.

## Install safety

All store-managed plugin installs additionally:

- reject ZIP path traversal, encrypted ZIPs, and ZIP symlinks;
- cap archive download size, extracted size, and file count;
- stage replacements and roll back a failed update;
- constrain lifecycle writes to `FEEDBACK_PLUGINS_DIR`;
- keep Plugin Store cache/config state under `CONFIG_DIR/plugin_store`;
- refuse to modify/remove Plugin Store itself;
- require a custom mutation header so cross-origin HTML forms cannot trigger management operations.

## Optional configuration

For ZIP/manual installs of Plugin Store, the official registry URL can be overridden explicitly:

```yaml
environment:
  FEEDBACK_PLUGIN_STORE_REGISTRY_URL: "https://raw.githubusercontent.com/irnutsmurt/feedBack-plugin-store/main/registry.yaml"
```

Optional safety limits:

```yaml
environment:
  FEEDBACK_PLUGIN_STORE_MAX_ARCHIVE_MB: "100"
  FEEDBACK_PLUGIN_STORE_MAX_EXTRACT_MB: "500"
```

For non-standard Host packaging where `/app/VERSION` is unavailable:

```yaml
environment:
  FEEDBACK_HOST_VERSION: "0.3.0"
```

## API

Catalog and lifecycle:

- `GET /api/plugins/plugin_store/catalog`
- `GET /api/plugins/plugin_store/catalog?refresh=true`
- `POST /api/plugins/plugin_store/install/{store_id}/{plugin_id}`
- `POST /api/plugins/plugin_store/update/{store_id}/{plugin_id}`
- `DELETE /api/plugins/plugin_store/remove/{store_id}/{plugin_id}`

Third-party stores:

- `POST /api/plugins/plugin_store/stores`
- `DELETE /api/plugins/plugin_store/stores/{store_id}`

Runtime:

- `GET /api/plugins/plugin_store/instance`
- `POST /api/plugins/plugin_store/restart`

The original v0.1 official-only lifecycle routes remain available for compatibility.

Mutation endpoints require `X-FeedBack-Plugin-Store: 1`.

## Tests

```bash
pytest -q
node --check screen.js
python -m py_compile routes.py storelib.py
```

## License

AGPL-3.0-only
