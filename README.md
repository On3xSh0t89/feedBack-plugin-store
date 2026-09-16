# feedBack Plugin Store

A small curated plugin store for [feedBack](https://github.com/got-feedBack/feedBack), maintained at:

**https://github.com/irnutsmurt/feedBack-plugin-store**

## What it does

- Reads a lightweight `registry.yaml` catalog hosted in this repository.
- Uses HTTP conditional requests (`ETag` / `If-None-Match`) so an unchanged catalog is not re-downloaded.
- Shows **Install**, **Update**, or **Remove** based on the plugin's on-disk `plugin.json`.
- Downloads plugins directly from the official `got-feedBack` GitHub organization as ZIP archives.
- Validates plugin id and version before installation.
- Requires a feedBack restart after install/update/remove.

The bundled registry was generated from the official organization repository list and each repository's actual `plugin.json`. Repositories marked `private: true` and the plugin-spec documentation repository are not offered.

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

Then restart feedBack:

```bash
docker compose restart
```

The clone directory must be named `plugin_store` because the feedBack plugin id is `plugin_store`.

## Registry updates

When installed with `git clone`, Plugin Store derives the raw `registry.yaml` URL from its own Git origin. Opening/refreshing the store checks that file on GitHub. It caches GitHub's `ETag` and sends `If-None-Match`; an unchanged registry produces `304 Not Modified` and the cached copy is reused.

The running plugin does **not** enumerate arbitrary GitHub repositories. `registry.yaml` is the allowlist.

To refresh the registry from the official feedBack organization:

```bash
python tools/sync_registry.py
```

The sync tool:

1. Enumerates public repositories under `got-feedBack`.
2. Selects `feedBack-plugin-*` repositories.
3. Reads each default branch's root `plugin.json`.
4. Skips `feedBack-plugin-spec`, forks, missing manifests, and manifests with `private: true`.
5. Writes current id/name/version/description/repository metadata into `registry.yaml`.

An optional `GITHUB_TOKEN` can be supplied to avoid anonymous GitHub API rate limits.

## Registry format

```yaml
schema: 1
source: https://github.com/orgs/got-feedBack/repositories
plugins:
  - id: note_detect
    name: Note Detection
    description: Real-time note detection and scoring from your instrument.
    version: 1.32.0
    repository: https://github.com/got-feedBack/feedBack-plugin-notedetect
    ref: main
```

## Security model

Installing a feedBack plugin installs executable application code. Plugin Store therefore:

- accepts only repositories owned by `got-feedBack`;
- never accepts arbitrary user-supplied plugin URLs;
- rejects ZIP path traversal and symlinks;
- caps archive download and extraction sizes;
- validates downloaded `plugin.json` id and version against the registry;
- stages replacements and rolls back a failed update;
- refuses to write outside `CONFIG_DIR`;
- refuses to modify/remove itself;
- requires a custom request header on mutation endpoints.

## Optional configuration

For ZIP/manual installs of Plugin Store, force the registry URL explicitly:

```yaml
environment:
  FEEDBACK_PLUGIN_STORE_REGISTRY_URL: "https://raw.githubusercontent.com/irnutsmurt/feedBack-plugin-store/main/registry.yaml"
```

Optional archive safety limits:

```yaml
environment:
  FEEDBACK_PLUGIN_STORE_MAX_ARCHIVE_MB: "100"
  FEEDBACK_PLUGIN_STORE_MAX_EXTRACT_MB: "500"
```

## API

- `GET /api/plugins/plugin_store/catalog`
- `GET /api/plugins/plugin_store/catalog?refresh=true`
- `POST /api/plugins/plugin_store/install/{id}`
- `POST /api/plugins/plugin_store/update/{id}`
- `DELETE /api/plugins/plugin_store/remove/{id}`

Mutation endpoints require `X-FeedBack-Plugin-Store: 1`.

## License

AGPL-3.0-only
