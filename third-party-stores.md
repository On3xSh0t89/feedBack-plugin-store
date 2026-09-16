# Third-party store format and trust model

Plugin Store v0.2 supports community catalogs while keeping them clearly separated from the official feedBack catalog.

## Minimal catalog

```yaml
schema: 1
store:
  name: Example Community Store
plugins:
  - id: example_plugin
    name: Example Plugin
    description: Example plugin.
    version: 1.0.0
    repository: https://github.com/example/feedBack-plugin-example
    ref: main
```

`store.name` is required. `store.description` and `store.homepage` are optional.

Each plugin requires:

- `id` — must match feedBack's plugin-id rules and the repository's `plugin.json`.
- `name` — display name.
- `description` — listing text (may be empty).
- `version` — semantic version and must equal the repository's `plugin.json` version.
- `repository` — HTTPS GitHub repository.
- `ref` — branch to fetch; defaults to `main` when omitted.

## Store URL requirements

For v0.2, store YAML must be served from:

- `https://raw.githubusercontent.com/...`
- `https://<user-or-org>.github.io/...`

A pasted GitHub browser URL such as:

```text
https://github.com/example/community-store/blob/main/store.yaml
```

is automatically converted to its raw-content URL.

The restriction is intentional: Plugin Store runs inside the feedBack server and should not become a generic URL fetcher that can be pointed at private LAN services.

## Validation

A store is not saved until its YAML parses and conforms to store schema 1. Plugin Store then reads the root `plugin.json` from every listed repository/ref and checks that the catalog's id/version agree with the plugin itself.

A store is rejected when it attempts to claim an official feedBack plugin id or an id already claimed by another configured third-party store.

If a valid plugin declares a `minHost` newer than the running feedBack Host, the store can still be added, but that plugin is shown as incompatible and its Install button is disabled. The backend repeats the check before installation.

Immediately before a ZIP is installed, Plugin Store verifies the downloaded manifest again and confirms its declared files exist.

## Security warning

feedBack plugins are executable code. A third-party plugin can contain Python backend routes and browser JavaScript. Installing one grants that code the permissions available to feedBack/container runtime.

Plugin Store therefore labels community catalogs **THIRD-PARTY STORE**, labels their plugins **THIRD-PARTY**, and requires explicit acknowledgement before both adding a store and installing/updating code from it.

Registry validation is not a malware scanner and does not establish that third-party code is trustworthy.
