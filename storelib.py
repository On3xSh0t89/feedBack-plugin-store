"""Pure helpers for the feedBack Plugin Store.

No filesystem or network I/O occurs at import time.
"""

from __future__ import annotations

import configparser
import io
import json
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

OFFICIAL_GITHUB_ORG = "got-feedback"
REGISTRY_CACHE_SECONDS = 300
MAX_REGISTRY_BYTES = 1024 * 1024
DEFAULT_MAX_ARCHIVE_MB = 100
DEFAULT_MAX_EXTRACT_MB = 500
DEFAULT_MAX_FILES = 10000

USER_AGENT = "feedBack-plugin-store/0.1.0"


class StoreError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True))


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def semver_key(version: str) -> tuple[int, int, int, tuple[Any, ...]] | None:
    match = SEMVER_RE.fullmatch(str(version or ""))
    if not match:
        return None

    major, minor, patch, prerelease = match.groups()
    if prerelease is None:
        pre_key: tuple[Any, ...] = ((1, ""),)
    else:
        parts = []
        for part in prerelease.split("."):
            if part.isdigit():
                parts.append((0, int(part)))
            else:
                parts.append((1, part))
        # Prereleases sort below a final release.
        pre_key = ((0, ""),) + tuple(parts)

    return int(major), int(minor), int(patch), pre_key


def compare_versions(installed: str, available: str) -> int:
    """Return -1 if installed < available, 0 if equal, 1 if installed > available."""
    if installed == available:
        return 0

    a = semver_key(installed)
    b = semver_key(available)
    if a is None or b is None:
        # Unknown version shape: different strings mean "potential update".
        return -1

    a_base = a[:3]
    b_base = b[:3]
    if a_base < b_base:
        return -1
    if a_base > b_base:
        return 1

    def prerelease_parts(version: str):
        m = SEMVER_RE.fullmatch(version)
        raw = m.group(4) if m else None
        if raw is None:
            return None
        out = []
        for part in raw.split("."):
            out.append(int(part) if part.isdigit() else part)
        return out

    ap = prerelease_parts(installed)
    bp = prerelease_parts(available)
    if ap is None and bp is None:
        return 0
    if ap is None:
        return 1
    if bp is None:
        return -1

    for left, right in zip(ap, bp):
        if left == right:
            continue
        if isinstance(left, int) and isinstance(right, str):
            return -1
        if isinstance(left, str) and isinstance(right, int):
            return 1
        return -1 if left < right else 1
    if len(ap) == len(bp):
        return 0
    return -1 if len(ap) < len(bp) else 1


def parse_github_repo(url: str) -> tuple[str, str] | None:
    raw = str(url or "").strip()

    patterns = (
        r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, raw, re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
    return None


def validate_registry_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise StoreError(
            "Registry URL must be an HTTPS raw.githubusercontent.com URL.",
            500,
        )
    return url


def _raw_registry_url(owner: str, repo: str, branch: str = "main") -> str:
    owner_q = urllib.parse.quote(owner, safe="")
    repo_q = urllib.parse.quote(repo, safe="")
    branch_q = urllib.parse.quote(branch, safe="")
    return (
        f"https://raw.githubusercontent.com/"
        f"{owner_q}/{repo_q}/{branch_q}/registry.yaml"
    )


def discover_registry_url(plugin_dir: Path) -> str | None:
    configured = os.environ.get("FEEDBACK_PLUGIN_STORE_REGISTRY_URL", "").strip()
    if configured:
        return validate_registry_url(configured)

    # Preferred: derive the registry from the git origin when this plugin was
    # installed with git clone.
    git_dir = plugin_dir / ".git"
    if git_dir.is_dir():
        config_path = git_dir / "config"
        head_path = git_dir / "HEAD"
        if config_path.is_file():
            parser = configparser.ConfigParser()
            try:
                parser.read(config_path, encoding="utf-8")
                origin = parser.get('remote "origin"', "url")
            except (configparser.Error, KeyError, OSError):
                origin = ""

            parsed_repo = parse_github_repo(origin)
            if parsed_repo:
                owner, repo = parsed_repo
                branch = "main"
                try:
                    head = head_path.read_text(encoding="utf-8").strip()
                    prefix = "ref: refs/heads/"
                    if head.startswith(prefix):
                        branch = head[len(prefix):]
                except OSError:
                    pass
                return _raw_registry_url(owner, repo, branch)

    # ZIP/manual installs have no .git directory. Fall back to the plugin's
    # declared GitHub homepage so they still receive live registry updates.
    try:
        manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
        homepage = str(manifest.get("homepage") or "") if isinstance(manifest, dict) else ""
    except (OSError, json.JSONDecodeError):
        homepage = ""

    parsed_repo = parse_github_repo(homepage)
    if parsed_repo:
        owner, repo = parsed_repo
        return _raw_registry_url(owner, repo, "main")

    return None


def validate_registry(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StoreError("Registry root must be a YAML mapping.", 502)
    if data.get("schema") != 1:
        raise StoreError("Unsupported registry schema; expected schema: 1.", 502)

    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise StoreError("Registry must contain a plugins list.", 502)

    seen = set()
    clean_plugins = []

    for item in plugins:
        if not isinstance(item, dict):
            raise StoreError("Every registry plugin must be a mapping.", 502)

        plugin_id = str(item.get("id", ""))
        name = str(item.get("name", ""))
        description = str(item.get("description", ""))
        version = str(item.get("version", ""))
        repository = str(item.get("repository", ""))
        ref = str(item.get("ref", "main"))

        if not ID_RE.fullmatch(plugin_id):
            raise StoreError(f"Invalid plugin id in registry: {plugin_id!r}", 502)
        if plugin_id in seen:
            raise StoreError(f"Duplicate plugin id in registry: {plugin_id}", 502)
        seen.add(plugin_id)

        if not name or len(name) > 120:
            raise StoreError(f"Invalid name for plugin {plugin_id}", 502)
        if len(description) > 1000:
            raise StoreError(f"Description too long for plugin {plugin_id}", 502)
        if not SEMVER_RE.fullmatch(version):
            raise StoreError(
                f"Registry version for {plugin_id} must be semantic versioning.",
                502,
            )
        if not REF_RE.fullmatch(ref) or ".." in ref or "//" in ref:
            raise StoreError(f"Invalid git ref for plugin {plugin_id}", 502)

        repo_parts = parse_github_repo(repository)
        if not repo_parts:
            raise StoreError(
                f"Plugin {plugin_id} repository must be a GitHub repository.",
                502,
            )

        owner, repo = repo_parts
        if owner.lower() != OFFICIAL_GITHUB_ORG:
            raise StoreError(
                f"Plugin {plugin_id} is not hosted by the official got-feedBack organization.",
                502,
            )

        clean_plugins.append(
            {
                "id": plugin_id,
                "name": name,
                "description": description,
                "version": version,
                "repository": f"https://github.com/{owner}/{repo}",
                "ref": ref,
            }
        )

    return {"schema": 1, "plugins": clean_plugins}


def load_registry_yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StoreError(f"Registry YAML is invalid: {exc}", 502) from exc
    return validate_registry(data)


def archive_url(entry: dict[str, Any]) -> str:
    parsed = parse_github_repo(entry["repository"])
    if not parsed:
        raise StoreError("Invalid GitHub repository.", 500)
    owner, repo = parsed
    if owner.lower() != OFFICIAL_GITHUB_ORG:
        raise StoreError("Refusing to download a non-official plugin.", 403)
    ref = entry["ref"]
    owner_q = urllib.parse.quote(owner, safe="")
    repo_q = urllib.parse.quote(repo, safe="")
    ref_path = "/".join(urllib.parse.quote(p, safe="") for p in ref.split("/"))
    return f"https://codeload.github.com/{owner_q}/{repo_q}/zip/refs/heads/{ref_path}"


def read_plugin_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest_path = plugin_dir / "plugin.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StoreError("Downloaded plugin has no plugin.json.", 502) from exc
    except json.JSONDecodeError as exc:
        raise StoreError("Downloaded plugin has invalid plugin.json.", 502) from exc

    if not isinstance(data, dict):
        raise StoreError("plugin.json must contain a JSON object.", 502)
    return data


def ensure_within(child: Path, parent: Path) -> None:
    child_r = child.resolve(strict=False)
    parent_r = parent.resolve(strict=False)
    try:
        child_r.relative_to(parent_r)
    except ValueError as exc:
        raise StoreError("Path escaped the allowed plugin-store directory.", 500) from exc


def safe_target(plugin_root: Path, plugin_id: str) -> Path:
    if not ID_RE.fullmatch(plugin_id):
        raise StoreError("Invalid plugin id.")
    target = plugin_root / plugin_id
    ensure_within(target, plugin_root)
    return target


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0o170000
    return mode == stat.S_IFLNK


def safe_extract_zip(
    archive: Path,
    output_dir: Path,
    *,
    max_extract_bytes: int,
    max_files: int,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    total_size = 0
    file_count = 0

    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.flag_bits & 0x1:
                raise StoreError("Encrypted plugin archives are not supported.", 502)
            if _is_zip_symlink(info):
                raise StoreError("Plugin archive contains a symlink; refusing extraction.", 502)

            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise StoreError("Plugin archive contains an unsafe path.", 502)

            total_size += info.file_size
            if total_size > max_extract_bytes:
                raise StoreError("Plugin archive expands beyond the configured safety limit.", 413)

            if not info.is_dir():
                file_count += 1
                if file_count > max_files:
                    raise StoreError("Plugin archive contains too many files.", 413)

            if not path.parts:
                continue

            destination = output_dir.joinpath(*path.parts)
            ensure_within(destination, output_dir)

            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


def locate_plugin_root(extract_dir: Path) -> Path:
    candidates = []
    root_manifest = extract_dir / "plugin.json"
    if root_manifest.is_file():
        candidates.append(extract_dir)

    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "plugin.json").is_file():
            candidates.append(child)

    if len(candidates) != 1:
        raise StoreError(
            "Plugin archive must contain exactly one top-level plugin.json.",
            502,
        )
    return candidates[0]


class PluginStore:
    def __init__(self, config_dir: Path, plugin_dir: Path, log: Any):
        self.config_dir = config_dir.resolve(strict=False)
        self.plugin_dir = plugin_dir.resolve(strict=False)
        self.log = log

        configured_root = os.environ.get("FEEDBACK_PLUGINS_DIR", "").strip()
        if configured_root:
            self.plugin_root = Path(configured_root).resolve(strict=False)
        else:
            self.plugin_root = (self.config_dir / "user-plugins").resolve(strict=False)

        # FEEDBACK_PLUGINS_DIR is a Host/admin-controlled plugin root and may
        # legitimately live outside CONFIG_DIR (for example /user-plugins).
        # Keep store state/cache under CONFIG_DIR, but confine all plugin
        # lifecycle operations to this dedicated plugin root.
        if not self.plugin_root.is_absolute():
            raise StoreError("FEEDBACK_PLUGINS_DIR must resolve to an absolute path.", 500)
        if self.plugin_root == Path("/"):
            raise StoreError("Refusing to use the filesystem root as FEEDBACK_PLUGINS_DIR.", 500)

        self.state_dir = self.config_dir / "plugin_store"
        self.cache_path = self.state_dir / "registry.yaml"
        self.meta_path = self.state_dir / "registry-meta.json"
        self.tmp_dir = self.state_dir / "tmp"
        self.registry_url = discover_registry_url(self.plugin_dir)

        max_archive_mb = int(
            os.environ.get("FEEDBACK_PLUGIN_STORE_MAX_ARCHIVE_MB", DEFAULT_MAX_ARCHIVE_MB)
        )
        max_extract_mb = int(
            os.environ.get("FEEDBACK_PLUGIN_STORE_MAX_EXTRACT_MB", DEFAULT_MAX_EXTRACT_MB)
        )
        self.max_archive_bytes = max_archive_mb * 1024 * 1024
        self.max_extract_bytes = max_extract_mb * 1024 * 1024
        self.max_files = DEFAULT_MAX_FILES

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.plugin_root.mkdir(parents=True, exist_ok=True)

    def _bundled_registry(self) -> tuple[dict[str, Any], str]:
        bundled = self.plugin_dir / "registry.yaml"
        text = bundled.read_text(encoding="utf-8")
        return load_registry_yaml(text), "bundled"

    def _cached_registry(self) -> tuple[dict[str, Any], str] | None:
        try:
            text = self.cache_path.read_text(encoding="utf-8")
        except OSError:
            return None
        return load_registry_yaml(text), "cache"

    def _fetch_registry(self, force: bool = False) -> dict[str, Any]:
        if not self.registry_url:
            registry, source = self._bundled_registry()
            return {
                "registry": registry,
                "source": source,
                "source_url": None,
                "changed": False,
                "stale": False,
                "warning": None,
            }

        meta = read_json(self.meta_path, {}) or {}
        now = time.time()

        if (
            not force
            and self.cache_path.is_file()
            and now - float(meta.get("checked_at", 0)) < REGISTRY_CACHE_SECONDS
        ):
            cached = self._cached_registry()
            if cached:
                registry, _ = cached
                return {
                    "registry": registry,
                    "source": "cache",
                    "source_url": self.registry_url,
                    "changed": False,
                    "stale": False,
                    "warning": None,
                }

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/yaml, text/plain;q=0.9, */*;q=0.1",
        }
        if meta.get("etag"):
            headers["If-None-Match"] = str(meta["etag"])
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = str(meta["last_modified"])

        request = urllib.request.Request(self.registry_url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                content = response.read(MAX_REGISTRY_BYTES + 1)
                if len(content) > MAX_REGISTRY_BYTES:
                    raise StoreError("Remote registry is too large.", 502)

                text = content.decode("utf-8")
                registry = load_registry_yaml(text)
                atomic_write_text(self.cache_path, text)

                new_meta = {
                    "checked_at": now,
                    "etag": response.headers.get("ETag"),
                    "last_modified": response.headers.get("Last-Modified"),
                    "source_url": self.registry_url,
                }
                atomic_write_json(self.meta_path, new_meta)
                return {
                    "registry": registry,
                    "source": "remote",
                    "source_url": self.registry_url,
                    "changed": True,
                    "stale": False,
                    "warning": None,
                }

        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                meta["checked_at"] = now
                atomic_write_json(self.meta_path, meta)
                cached = self._cached_registry()
                if cached:
                    registry, _ = cached
                    return {
                        "registry": registry,
                        "source": "cache",
                        "source_url": self.registry_url,
                        "changed": False,
                        "stale": False,
                        "warning": None,
                    }
            fetch_error = f"Registry request failed: HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            fetch_error = f"Registry request failed: {exc}"
        except StoreError:
            raise

        cached = self._cached_registry()
        if cached:
            registry, _ = cached
            return {
                "registry": registry,
                "source": "cache",
                "source_url": self.registry_url,
                "changed": False,
                "stale": True,
                "warning": fetch_error,
            }

        registry, _ = self._bundled_registry()
        return {
            "registry": registry,
            "source": "bundled",
            "source_url": self.registry_url,
            "changed": False,
            "stale": True,
            "warning": fetch_error,
        }

    def catalog(self, force_refresh: bool = False) -> dict[str, Any]:
        info = self._fetch_registry(force=force_refresh)
        output = []

        for entry in info["registry"]["plugins"]:
            target = safe_target(self.plugin_root, entry["id"])
            state = {
                **entry,
                "installed": False,
                "installed_version": None,
                "status": "available",
                "update_available": False,
            }

            if target.exists():
                try:
                    if target.is_symlink():
                        raise StoreError("Installed plugin path is a symlink.")
                    manifest = read_plugin_manifest(target)
                    if manifest.get("id") != entry["id"]:
                        raise StoreError("Installed plugin id does not match its directory.")
                    installed_version = str(manifest.get("version") or "")
                    state["installed"] = True
                    state["installed_version"] = installed_version or None

                    comparison = compare_versions(installed_version, entry["version"])
                    if comparison < 0:
                        state["status"] = "update_available"
                        state["update_available"] = True
                    elif comparison > 0:
                        state["status"] = "local_newer"
                    else:
                        state["status"] = "installed"
                except StoreError as exc:
                    state["installed"] = True
                    state["status"] = "broken"
                    state["error"] = exc.message

            output.append(state)

        return {
            "schema": 1,
            "plugins": output,
            "registry": {
                "source": info["source"],
                "source_url": info["source_url"],
                "changed": info["changed"],
                "stale": info["stale"],
                "warning": info["warning"],
            },
            "plugin_root": str(self.plugin_root),
        }

    def _entry(self, plugin_id: str) -> dict[str, Any]:
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)
        info = self._fetch_registry(force=False)
        for entry in info["registry"]["plugins"]:
            if entry["id"] == plugin_id:
                return entry
        raise StoreError("Plugin is not present in the curated registry.", 404)

    def _download(self, entry: dict[str, Any], destination: Path) -> None:
        url = archive_url(entry)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/zip, application/octet-stream;q=0.9",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as fh:
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > self.max_archive_bytes:
                        raise StoreError(
                            "Plugin archive exceeds the configured download safety limit.",
                            413,
                        )
                    fh.write(chunk)
        except StoreError:
            raise
        except urllib.error.HTTPError as exc:
            raise StoreError(
                f"GitHub returned HTTP {exc.code} while downloading the plugin.",
                502,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StoreError(f"Plugin download failed: {exc}", 502) from exc

    def install(self, plugin_id: str, *, replace: bool) -> dict[str, Any]:
        if plugin_id == "plugin_store":
            raise StoreError("The Plugin Store cannot modify itself.", 409)

        entry = self._entry(plugin_id)
        target = safe_target(self.plugin_root, plugin_id)

        if target.exists() and not replace:
            raise StoreError("Plugin is already installed.", 409)
        if not target.exists() and replace:
            raise StoreError("Plugin is not installed.", 404)
        if target.is_symlink():
            raise StoreError("Refusing to replace a symlinked plugin directory.", 409)

        operation_id = uuid.uuid4().hex
        work_dir = self.tmp_dir / operation_id
        archive = work_dir / "plugin.zip"
        extracted = work_dir / "extracted"
        staging = self.plugin_root / f".plugin_store-staging-{operation_id}"
        backup = self.plugin_root / f".plugin_store-backup-{plugin_id}-{operation_id}"

        ensure_within(work_dir, self.state_dir)
        ensure_within(staging, self.plugin_root)
        ensure_within(backup, self.plugin_root)

        work_dir.mkdir(parents=True, exist_ok=False)

        try:
            self._download(entry, archive)
            safe_extract_zip(
                archive,
                extracted,
                max_extract_bytes=self.max_extract_bytes,
                max_files=self.max_files,
            )

            source_root = locate_plugin_root(extracted)
            manifest = read_plugin_manifest(source_root)

            manifest_id = manifest.get("id")
            manifest_version = str(manifest.get("version") or "")

            if manifest_id != plugin_id:
                raise StoreError(
                    f"Downloaded plugin id {manifest_id!r} does not match registry id {plugin_id!r}.",
                    502,
                )
            if source_root.name == plugin_id:
                pass  # Nice when repositories already archive with the plugin id.
            if manifest_version != entry["version"]:
                raise StoreError(
                    f"Registry version {entry['version']} does not match downloaded "
                    f"plugin version {manifest_version or '<missing>'}.",
                    502,
                )

            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source_root, staging, symlinks=False)

            if replace:
                os.replace(target, backup)

            try:
                os.replace(staging, target)
            except Exception:
                if replace and backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise

            if backup.exists():
                shutil.rmtree(backup)

            self.log.info(
                "plugin_store_install_complete",
                extra={
                    "plugin_id": plugin_id,
                    "version": entry["version"],
                    "replace": replace,
                },
            )

            return {
                "ok": True,
                "plugin_id": plugin_id,
                "version": entry["version"],
                "operation": "update" if replace else "install",
                "restart_required": True,
            }

        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
            # A backup is intentionally left in place only if rollback itself failed.

    def remove(self, plugin_id: str) -> dict[str, Any]:
        if plugin_id == "plugin_store":
            raise StoreError("The Plugin Store cannot remove itself.", 409)

        # Only registry-listed plugins are manageable.
        self._entry(plugin_id)
        target = safe_target(self.plugin_root, plugin_id)

        if not target.exists():
            raise StoreError("Plugin is not installed.", 404)
        if target.is_symlink():
            raise StoreError("Refusing to remove a symlinked plugin directory.", 409)

        manifest = read_plugin_manifest(target)
        if manifest.get("id") != plugin_id:
            raise StoreError(
                "Installed plugin manifest does not match the requested plugin id.",
                409,
            )

        shutil.rmtree(target)
        self.log.info(
            "plugin_store_remove_complete",
            extra={"plugin_id": plugin_id},
        )

        return {
            "ok": True,
            "plugin_id": plugin_id,
            "operation": "remove",
            "restart_required": True,
        }
