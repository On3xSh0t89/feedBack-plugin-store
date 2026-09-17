"""Backend helpers for the feedBack Plugin Store.

The module intentionally performs no filesystem or network I/O at import time.
All state/network work starts from PluginStore(), which is constructed by
routes.setup().
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import stat
import sys
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

STORE_SCHEMA = 1
OFFICIAL_STORE_ID = "official"
OFFICIAL_STORE_NAME = "Official feedBack Plugins"
DIRECT_STORE_ID = "direct"
DIRECT_STORE_NAME = "Direct GitHub Installs"
OFFICIAL_GITHUB_ORG = "got-feedback"
PLUGIN_SPEC_MAJOR = 1
REGISTRY_CACHE_SECONDS = 300
MAX_REGISTRY_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_THIRD_PARTY_STORES = 20
MAX_PLUGINS_PER_STORE = 100
DEFAULT_MAX_ARCHIVE_MB = 100
DEFAULT_MAX_EXTRACT_MB = 500
DEFAULT_MAX_FILES = 10000
DEFAULT_BACKUPS_PER_PLUGIN = 2
MAX_BACKUPS_PER_PLUGIN = 10

USER_AGENT = "feedBack-plugin-store/0.4.0"
SELF_UPDATE_CACHE_SECONDS = 300
MAX_SELF_MANIFEST_BYTES = 128 * 1024


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
    atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def semver_key(version: str) -> tuple[int, int, int, list[Any] | None] | None:
    match = SEMVER_RE.fullmatch(str(version or ""))
    if not match:
        return None

    major, minor, patch, prerelease = match.groups()
    pre: list[Any] | None = None
    if prerelease is not None:
        pre = []
        for part in prerelease.split("."):
            pre.append(int(part) if part.isdigit() else part)
    return int(major), int(minor), int(patch), pre


def compare_versions(left: str, right: str) -> int:
    """Return -1 when left < right, 0 when equal, 1 when left > right."""
    if left == right:
        return 0

    a = semver_key(left)
    b = semver_key(right)
    if a is None or b is None:
        return -1

    if a[:3] < b[:3]:
        return -1
    if a[:3] > b[:3]:
        return 1

    ap, bp = a[3], b[3]
    if ap is None and bp is None:
        return 0
    if ap is None:
        return 1
    if bp is None:
        return -1

    for av, bv in zip(ap, bp):
        if av == bv:
            continue
        if isinstance(av, int) and isinstance(bv, str):
            return -1
        if isinstance(av, str) and isinstance(bv, int):
            return 1
        return -1 if av < bv else 1
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


def parse_https_github_repo(url: str) -> tuple[str, str] | None:
    raw = str(url or "").strip()
    match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?",
        raw,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def normalize_registry_url(url: str) -> str:
    """Validate/normalise a community store URL.

    v0.2 intentionally limits registries to GitHub-hosted HTTPS content. That
    gives community stores useful flexibility without turning the feedBack
    container into an arbitrary URL fetcher on the user's LAN.
    """
    raw = str(url or "").strip()
    if not raw or len(raw) > 2048:
        raise StoreError("Enter a valid third-party store URL.", 400)

    parsed = urllib.parse.urlparse(raw)
    host = (parsed.hostname or "").lower()

    # Friendly conversion for a GitHub 'blob' link pasted from the browser.
    if parsed.scheme == "https" and host == "github.com":
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repo, _blob, ref = parts[:4]
            file_path = "/".join(parts[4:])
            return (
                "https://raw.githubusercontent.com/"
                f"{urllib.parse.quote(owner, safe='')}/"
                f"{urllib.parse.quote(repo, safe='')}/"
                f"{urllib.parse.quote(ref, safe='')}/"
                + "/".join(urllib.parse.quote(p, safe="") for p in file_path.split("/"))
            )
        raise StoreError(
            "GitHub store links must point directly to a YAML file (for example a blob/.../store.yaml URL).",
            400,
        )

    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise StoreError("Third-party stores must use an HTTPS URL.", 400)

    allowed = host == "raw.githubusercontent.com" or host.endswith(".github.io")
    if not allowed:
        raise StoreError(
            "Third-party store YAML must be hosted on GitHub (raw.githubusercontent.com or GitHub Pages).",
            400,
        )

    if not parsed.path or parsed.path.endswith("/"):
        raise StoreError("The store URL must point to a YAML file.", 400)

    return urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, "", parsed.query, "")
    )


def _raw_registry_url(owner: str, repo: str, branch: str = "main") -> str:
    owner_q = urllib.parse.quote(owner, safe="")
    repo_q = urllib.parse.quote(repo, safe="")
    branch_q = urllib.parse.quote(branch, safe="")
    return f"https://raw.githubusercontent.com/{owner_q}/{repo_q}/{branch_q}/registry.yaml"


def discover_registry_url(plugin_dir: Path) -> str | None:
    configured = os.environ.get("FEEDBACK_PLUGIN_STORE_REGISTRY_URL", "").strip()
    if configured:
        # The official catalog may be hosted only on GitHub as well.
        return normalize_registry_url(configured)

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


def _validate_ref(ref: str, plugin_id: str) -> str:
    if not REF_RE.fullmatch(ref) or ".." in ref or "//" in ref:
        raise StoreError(f"Invalid git ref for plugin {plugin_id}", 502)
    return ref


def _validate_plugin_entry(item: Any, *, official: bool) -> dict[str, Any]:
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
    if not name or len(name) > 120:
        raise StoreError(f"Invalid name for plugin {plugin_id}", 502)
    if len(description) > 1000:
        raise StoreError(f"Description too long for plugin {plugin_id}", 502)
    if not SEMVER_RE.fullmatch(version):
        raise StoreError(
            f"Registry version for {plugin_id} must use semantic versioning.",
            502,
        )
    _validate_ref(ref, plugin_id)

    repo_parts = parse_https_github_repo(repository)
    if not repo_parts:
        raise StoreError(
            f"Plugin {plugin_id} repository must be an HTTPS github.com repository.",
            502,
        )
    owner, repo = repo_parts
    if official and owner.lower() != OFFICIAL_GITHUB_ORG:
        raise StoreError(
            f"Official plugin {plugin_id} is not hosted by the got-feedBack organization.",
            502,
        )

    return {
        "id": plugin_id,
        "name": name,
        "description": description,
        "version": version,
        "repository": f"https://github.com/{owner}/{repo}",
        "ref": ref,
    }


def validate_registry(data: Any, *, official: bool = True) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StoreError("Registry root must be a YAML mapping.", 502)
    if data.get("schema") != STORE_SCHEMA:
        raise StoreError(
            f"Unsupported store schema; expected schema: {STORE_SCHEMA}.",
            422,
        )

    plugins = data.get("plugins")
    if not isinstance(plugins, list):
        raise StoreError("Registry must contain a plugins list.", 422)
    if len(plugins) > MAX_PLUGINS_PER_STORE:
        raise StoreError(
            f"Registry contains too many plugins (maximum {MAX_PLUGINS_PER_STORE}).",
            422,
        )

    store_meta: dict[str, Any] = {}
    if not official:
        raw_store = data.get("store")
        if not isinstance(raw_store, dict):
            raise StoreError(
                "Third-party registry must contain a 'store' mapping with at least a name.",
                422,
            )
        store_name = str(raw_store.get("name", "")).strip()
        if not store_name or len(store_name) > 120:
            raise StoreError("Third-party store.name is required and must be 120 characters or fewer.", 422)
        store_description = str(raw_store.get("description", "")).strip()
        if len(store_description) > 500:
            raise StoreError("Third-party store.description is too long.", 422)
        homepage = str(raw_store.get("homepage", "")).strip()
        if homepage:
            homepage_parsed = urllib.parse.urlparse(homepage)
            if homepage_parsed.scheme != "https" or not homepage_parsed.hostname:
                raise StoreError("Third-party store.homepage must be an HTTPS URL.", 422)
        store_meta = {
            "name": store_name,
            "description": store_description,
            "homepage": homepage or None,
        }

    seen: set[str] = set()
    clean_plugins: list[dict[str, Any]] = []
    for item in plugins:
        clean = _validate_plugin_entry(item, official=official)
        if clean["id"] in seen:
            raise StoreError(f"Duplicate plugin id in registry: {clean['id']}", 422)
        seen.add(clean["id"])
        clean_plugins.append(clean)

    return {
        "schema": STORE_SCHEMA,
        "store": store_meta,
        "plugins": clean_plugins,
    }


def load_registry_yaml(text: str, *, official: bool = True) -> dict[str, Any]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StoreError(f"Registry YAML is invalid: {exc}", 422) from exc
    return validate_registry(data, official=official)


def archive_url(entry: dict[str, Any], *, official: bool = False) -> str:
    parsed = parse_https_github_repo(entry["repository"])
    if not parsed:
        raise StoreError("Invalid GitHub repository.", 500)
    owner, repo = parsed
    if official and owner.lower() != OFFICIAL_GITHUB_ORG:
        raise StoreError("Refusing to download a non-official plugin as an official plugin.", 403)
    ref = _validate_ref(str(entry["ref"]), str(entry["id"]))
    ref_kind = str(entry.get("ref_kind") or "head")
    if ref_kind not in {"head", "tag"}:
        raise StoreError("Invalid GitHub ref type.", 500)
    ref_namespace = "heads" if ref_kind == "head" else "tags"
    owner_q = urllib.parse.quote(owner, safe="")
    repo_q = urllib.parse.quote(repo, safe="")
    ref_path = "/".join(urllib.parse.quote(p, safe="") for p in ref.split("/"))
    return f"https://codeload.github.com/{owner_q}/{repo_q}/zip/refs/{ref_namespace}/{ref_path}"


def raw_manifest_url(entry: dict[str, Any]) -> str:
    parsed = parse_https_github_repo(entry["repository"])
    if not parsed:
        raise StoreError("Invalid GitHub repository.", 500)
    owner, repo = parsed
    ref = _validate_ref(str(entry["ref"]), str(entry["id"]))
    ref_path = "/".join(urllib.parse.quote(p, safe="") for p in ref.split("/"))
    return (
        "https://raw.githubusercontent.com/"
        f"{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(repo, safe='')}/{ref_path}/plugin.json"
    )


def read_plugin_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest_path = plugin_dir / "plugin.json"
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StoreError("Downloaded plugin has no plugin.json.", 422) from exc
    except json.JSONDecodeError as exc:
        raise StoreError("Downloaded plugin has invalid plugin.json.", 422) from exc
    if not isinstance(data, dict):
        raise StoreError("plugin.json must contain a JSON object.", 422)
    return data


def _safe_relative_manifest_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StoreError(f"plugin.json {field} must be a non-empty relative path.", 422)
    raw = value.strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise StoreError(f"plugin.json {field} contains an unsafe path.", 422)
    return raw


def validate_plugin_manifest(
    manifest: Any,
    *,
    expected_entry: dict[str, Any] | None = None,
    plugin_root: Path | None = None,
    host_version: str | None = None,
) -> dict[str, Any]:
    """Validate the stable feedBack plugin v1 contract used by this store.

    feedBack's manifest is forward-extensible, so unknown keys are deliberately
    ignored. Store-specific checks (semver and registry id/version agreement)
    are stricter because update/install safety depends on them.
    """
    if not isinstance(manifest, dict):
        raise StoreError("plugin.json must contain a JSON object.", 422)

    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not ID_RE.fullmatch(plugin_id):
        raise StoreError("plugin.json has an invalid or missing id.", 422)

    name = manifest.get("name")
    if name is not None and not isinstance(name, str):
        raise StoreError(f"Plugin {plugin_id} has an invalid name field.", 422)

    version = manifest.get("version")
    if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
        raise StoreError(
            f"Plugin {plugin_id} must declare a semantic version for Plugin Store management.",
            422,
        )

    if manifest.get("private") is True:
        raise StoreError(f"Plugin {plugin_id} is marked private and cannot be store-listed.", 422)

    if expected_entry is not None:
        if plugin_id != expected_entry["id"]:
            raise StoreError(
                f"Registry id {expected_entry['id']!r} does not match plugin.json id {plugin_id!r}.",
                422,
            )
        if version != expected_entry["version"]:
            raise StoreError(
                f"Registry version {expected_entry['version']} does not match plugin.json version {version} for {plugin_id}.",
                422,
            )

    min_host = manifest.get("minHost")
    if min_host is not None:
        if not isinstance(min_host, str) or not SEMVER_RE.fullmatch(min_host):
            raise StoreError(f"Plugin {plugin_id} has an invalid minHost value.", 422)

    for field in ("script", "screen", "styles", "routes", "tour"):
        if field in manifest:
            rel = _safe_relative_manifest_path(manifest[field], field)
            if plugin_root is not None and not (plugin_root / rel).is_file():
                raise StoreError(f"Plugin {plugin_id} references missing {field} file: {rel}", 422)

    settings = manifest.get("settings")
    if settings is not None:
        if not isinstance(settings, dict) or "html" not in settings:
            raise StoreError(f"Plugin {plugin_id} settings must declare settings.html.", 422)
        rel = _safe_relative_manifest_path(settings["html"], "settings.html")
        if plugin_root is not None and not (plugin_root / rel).is_file():
            raise StoreError(f"Plugin {plugin_id} references missing settings file: {rel}", 422)

    nav = manifest.get("nav")
    if nav is not None and not isinstance(nav, (dict, bool)):
        raise StoreError(f"Plugin {plugin_id} has an invalid nav declaration.", 422)

    standards = manifest.get("standards")
    if standards is not None and (
        not isinstance(standards, list)
        or any(not isinstance(item, str) for item in standards)
    ):
        raise StoreError(f"Plugin {plugin_id} has an invalid standards declaration.", 422)

    compatible = True
    compatibility_reason = None
    if min_host:
        if not host_version or not SEMVER_RE.fullmatch(host_version):
            compatible = False
            compatibility_reason = (
                f"Requires feedBack {min_host} or newer, but the running Host version could not be verified."
            )
        elif compare_versions(host_version, min_host) < 0:
            compatible = False
            compatibility_reason = (
                f"Requires feedBack {min_host} or newer; this Host is {host_version}."
            )

    return {
        "id": plugin_id,
        "version": version,
        "min_host": min_host,
        "compatible": compatible,
        "compatibility_reason": compatibility_reason,
        "manifest_major": PLUGIN_SPEC_MAJOR,
    }


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


def discover_builtin_plugins_dir() -> Path | None:
    """Locate the host's bundled plugins folder (the core ``plugins`` package).

    feedBack ships most official plugins inside the app itself. The host loader
    scans that folder after the user plugins folder, so the store needs it to
    recognise those plugins as already installed.
    """
    configured = os.environ.get("FEEDBACK_BUILTIN_PLUGINS_DIR", "").strip()
    if configured:
        return Path(configured).resolve(strict=False)
    host = sys.modules.get("plugins")
    candidate = getattr(host, "PLUGINS_DIR", None) if host is not None else None
    if candidate is None or not callable(getattr(host, "load_plugins", None)):
        return None
    path = Path(candidate).resolve(strict=False)
    return path if path.is_dir() else None


def index_plugin_folders(root: Path | None) -> dict[str, Path]:
    """Map manifest id -> folder for every plugin directly under ``root``.

    Mirrors the host loader: one level deep, sorted, first folder wins. Folder
    names are not trusted; a git clone is usually named after its repository.
    """
    index: dict[str, Path] = {}
    if root is None or not root.is_dir():
        return index
    for child in sorted(root.iterdir()):
        if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
            continue
        manifest_path = child / "plugin.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        plugin_id = manifest.get("id") if isinstance(manifest, dict) else None
        if isinstance(plugin_id, str) and plugin_id and plugin_id not in index:
            index[plugin_id] = child
    return index


def builtin_copy_is_locked(folder: Path, builtin_root: Path, plugin_id: str) -> bool:
    """True when the host always prefers this bundled copy over a user copy.

    Matches the host's ``_is_bundled`` rule: directly in the bundled folder,
    folder named after the id, and ``"bundled": true`` in the manifest.
    """
    if folder.parent != builtin_root or folder.name != plugin_id:
        return False
    try:
        manifest = json.loads((folder / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and bool(manifest.get("bundled"))


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
                raise StoreError("Encrypted plugin archives are not supported.", 422)
            if _is_zip_symlink(info):
                raise StoreError("Plugin archive contains a symlink; refusing extraction.", 422)

            path = PurePosixPath(info.filename)
            if path.is_absolute() or ".." in path.parts:
                raise StoreError("Plugin archive contains an unsafe path.", 422)

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
    candidates: list[Path] = []
    if (extract_dir / "plugin.json").is_file():
        candidates.append(extract_dir)
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "plugin.json").is_file():
            candidates.append(child)
    if len(candidates) != 1:
        raise StoreError(
            "Plugin archive must contain exactly one top-level plugin.json.",
            422,
        )
    return candidates[0]


def discover_host_version(plugin_dir: Path) -> str | None:
    env_version = os.environ.get("FEEDBACK_HOST_VERSION", "").strip()
    if SEMVER_RE.fullmatch(env_version):
        return env_version

    candidates = [
        Path("/app/VERSION"),
        Path.cwd() / "VERSION",
        plugin_dir.parent.parent / "VERSION",
    ]
    for path in candidates:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if SEMVER_RE.fullmatch(value):
            return value
    return None


def _store_id_for_url(url: str) -> str:
    return "third-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


class PluginStore:
    def __init__(
        self,
        config_dir: Path,
        plugin_dir: Path,
        log: Any,
        builtin_root: Path | None | bool = True,
    ):
        self.config_dir = config_dir.resolve(strict=False)
        self.plugin_dir = plugin_dir.resolve(strict=False)
        self.log = log

        # The desktop app still exports the legacy SLOPSMITH_PLUGINS_DIR name
        # (the host reads both via getenv_compat), so honour it as a fallback.
        configured_root = (
            os.environ.get("FEEDBACK_PLUGINS_DIR", "").strip()
            or os.environ.get("SLOPSMITH_PLUGINS_DIR", "").strip()
        )
        if configured_root:
            self.plugin_root = Path(configured_root).resolve(strict=False)
        else:
            self.plugin_root = (self.config_dir / "user-plugins").resolve(strict=False)
        if not self.plugin_root.is_absolute():
            raise StoreError("FEEDBACK_PLUGINS_DIR must resolve to an absolute path.", 500)
        if self.plugin_root == Path("/"):
            raise StoreError("Refusing to use the filesystem root as FEEDBACK_PLUGINS_DIR.", 500)

        self.state_dir = (self.config_dir / "plugin_store").resolve(strict=False)
        self.tmp_dir = self.state_dir / "tmp"
        self.third_cache_dir = self.state_dir / "third-party"
        self.stores_path = self.state_dir / "stores.json"
        self.managed_path = self.state_dir / "managed-plugins.json"
        self.exclusions_path = self.state_dir / "exclusions.json"
        self.backups_dir = self.state_dir / "backups"
        self.direct_cache_dir = self.state_dir / "direct"
        self.official_cache_path = self.state_dir / "registry.yaml"
        self.official_meta_path = self.state_dir / "registry-meta.json"
        # True = discover the host's bundled plugins folder; None/False = none.
        if builtin_root is True:
            builtin_root = discover_builtin_plugins_dir()
        self.builtin_root = (
            Path(builtin_root).resolve(strict=False) if builtin_root else None
        )
        if self.builtin_root == self.plugin_root:
            self.builtin_root = None
        self._folder_index_cache: dict[Path, tuple[int, dict[str, Path]]] = {}
        self.registry_url = discover_registry_url(self.plugin_dir)
        self.host_version = discover_host_version(self.plugin_dir)

        max_archive_mb = int(
            os.environ.get("FEEDBACK_PLUGIN_STORE_MAX_ARCHIVE_MB", DEFAULT_MAX_ARCHIVE_MB)
        )
        max_extract_mb = int(
            os.environ.get("FEEDBACK_PLUGIN_STORE_MAX_EXTRACT_MB", DEFAULT_MAX_EXTRACT_MB)
        )
        self.max_archive_bytes = max_archive_mb * 1024 * 1024
        self.max_extract_bytes = max_extract_mb * 1024 * 1024
        self.max_files = DEFAULT_MAX_FILES
        try:
            backups_per_plugin = int(
                os.environ.get(
                    "FEEDBACK_PLUGIN_STORE_BACKUPS_PER_PLUGIN",
                    DEFAULT_BACKUPS_PER_PLUGIN,
                )
            )
        except ValueError:
            backups_per_plugin = DEFAULT_BACKUPS_PER_PLUGIN
        self.backups_per_plugin = max(
            1,
            min(MAX_BACKUPS_PER_PLUGIN, backups_per_plugin),
        )

        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.third_cache_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.direct_cache_dir.mkdir(parents=True, exist_ok=True)
        self.plugin_root.mkdir(parents=True, exist_ok=True)

    def _log_info(self, *args: Any, **kwargs: Any) -> None:
        """Logging must never change the outcome of a completed store mutation."""
        try:
            self.log.info(*args, **kwargs)
        except Exception:
            # A logging formatter/adapter may reject extra fields. Store state is
            # more important than observability, so never turn success into HTTP 500.
            pass

    # ---------- low-level network/cache ----------

    def _fetch_text(
        self,
        url: str,
        *,
        max_bytes: int,
        timeout: int,
        headers: dict[str, str] | None = None,
    ) -> tuple[str, dict[str, str], str]:
        request_headers = {"User-Agent": USER_AGENT}
        request_headers.update(headers or {})
        request = urllib.request.Request(url, headers=request_headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            # Registry redirects stay on the GitHub-hosted allowlist. Raw plugin
            # manifest URLs are generated by us and also land on this allowlist.
            normalize_registry_url(final_url)
            content = response.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise StoreError("Remote response exceeded the configured safety limit.", 413)
            text = content.decode("utf-8")
            response_headers = {
                "etag": response.headers.get("ETag") or "",
                "last_modified": response.headers.get("Last-Modified") or "",
            }
            return text, response_headers, final_url


    def _local_store_manifest(self) -> dict[str, Any]:
        manifest_path = self.plugin_dir / "plugin.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError("Plugin Store plugin.json is unreadable.", 500) from exc
        if not isinstance(data, dict):
            raise StoreError("Plugin Store plugin.json must be a JSON object.", 500)
        return data

    def _self_manifest_url(self, manifest: dict[str, Any]) -> str | None:
        homepage = str(manifest.get("homepage") or "").strip()
        parsed = parse_github_repo(homepage)
        if not parsed:
            return None
        owner, repo = parsed
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repo, safe="")
        return (
            f"https://raw.githubusercontent.com/"
            f"{owner_q}/{repo_q}/main/plugin.json"
        )

    def self_update_status(self, force: bool = False) -> dict[str, Any]:
        """Check the Plugin Store's own GitHub manifest for a newer version."""
        local_manifest = self._local_store_manifest()
        local_version = str(local_manifest.get("version") or "")
        homepage = str(local_manifest.get("homepage") or "").strip() or None
        remote_url = self._self_manifest_url(local_manifest)

        result = {
            "installed_version": local_version or None,
            "available_version": None,
            "update_available": False,
            "homepage": homepage,
            "source_url": remote_url,
            "warning": None,
        }

        if not remote_url:
            result["warning"] = "Plugin Store homepage is not a supported GitHub repository URL."
            return result

        cache_path = self.state_dir / "self-update.json"
        cached = read_json(cache_path, {}) or {}
        now = time.time()

        if (
            not force
            and cached
            and now - float(cached.get("checked_at", 0)) < SELF_UPDATE_CACHE_SECONDS
        ):
            remote_version = str(cached.get("remote_version") or "")
            result["available_version"] = remote_version or None
            if local_version and remote_version:
                result["update_available"] = compare_versions(local_version, remote_version) < 0
            result["warning"] = cached.get("warning")
            return result

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.1",
            "Cache-Control": "no-cache",
        }
        if cached.get("etag"):
            headers["If-None-Match"] = str(cached["etag"])
        if cached.get("last_modified"):
            headers["If-Modified-Since"] = str(cached["last_modified"])

        request = urllib.request.Request(remote_url, headers=headers)

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read(MAX_SELF_MANIFEST_BYTES + 1)
                if len(payload) > MAX_SELF_MANIFEST_BYTES:
                    raise StoreError("Remote Plugin Store manifest is too large.", 502)

                remote = json.loads(payload.decode("utf-8"))
                if not isinstance(remote, dict):
                    raise StoreError("Remote Plugin Store manifest is not a JSON object.", 502)
                if remote.get("id") != "plugin_store":
                    raise StoreError("Remote Plugin Store manifest has the wrong plugin id.", 502)

                remote_version = str(remote.get("version") or "")
                if not SEMVER_RE.fullmatch(remote_version):
                    raise StoreError("Remote Plugin Store manifest has an invalid version.", 502)

                atomic_write_json(
                    cache_path,
                    {
                        "checked_at": now,
                        "remote_version": remote_version,
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"),
                        "warning": None,
                    },
                )
                result["available_version"] = remote_version
                if local_version:
                    result["update_available"] = compare_versions(local_version, remote_version) < 0
                return result

        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached.get("remote_version"):
                cached["checked_at"] = now
                cached["warning"] = None
                atomic_write_json(cache_path, cached)
                remote_version = str(cached.get("remote_version") or "")
                result["available_version"] = remote_version or None
                if local_version and remote_version:
                    result["update_available"] = compare_versions(local_version, remote_version) < 0
                return result
            warning = f"Plugin Store update check failed: HTTP {exc.code}"
        except StoreError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            warning = f"Plugin Store update check failed: {exc}"

        remote_version = str(cached.get("remote_version") or "")
        result["available_version"] = remote_version or None
        if local_version and remote_version:
            result["update_available"] = compare_versions(local_version, remote_version) < 0
        result["warning"] = warning

        atomic_write_json(
            cache_path,
            {
                "checked_at": now,
                "remote_version": remote_version or None,
                "etag": cached.get("etag"),
                "last_modified": cached.get("last_modified"),
                "warning": warning,
            },
        )
        return result

    def install_self_update(self) -> dict[str, Any]:
        """Download, validate, and atomically replace the Plugin Store itself.

        The currently loaded Python module remains alive until feedBack exits.
        routes.py schedules that restart only after this transaction succeeds.
        """
        status = self.self_update_status(force=True)
        if not status.get("update_available"):
            raise StoreError("No Plugin Store update is currently available.", 409)

        expected_version = str(status.get("available_version") or "")
        if not SEMVER_RE.fullmatch(expected_version):
            raise StoreError("The available Plugin Store version is invalid.", 502)

        local_manifest = self._local_store_manifest()
        local_version = str(local_manifest.get("version") or "")
        if not SEMVER_RE.fullmatch(local_version):
            raise StoreError("The installed Plugin Store version is invalid.", 500)
        if compare_versions(local_version, expected_version) >= 0:
            raise StoreError("No newer Plugin Store version is available.", 409)

        homepage = str(local_manifest.get("homepage") or "").strip()
        parsed = parse_https_github_repo(homepage)
        if not parsed:
            raise StoreError(
                "Plugin Store homepage is not a supported HTTPS GitHub repository URL.",
                500,
            )
        owner, repo = parsed
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repo, safe="")
        archive_url = (
            f"https://codeload.github.com/{owner_q}/{repo_q}/zip/refs/heads/main"
        )

        if self.plugin_dir.is_symlink():
            raise StoreError("Refusing to self-update a symlinked Plugin Store directory.", 409)
        if not self.plugin_dir.is_dir():
            raise StoreError("Plugin Store directory is unavailable.", 500)

        operation_id = uuid.uuid4().hex
        work_dir = self.tmp_dir / f"self-update-{operation_id}"
        archive = work_dir / "plugin-store.zip"
        extracted = work_dir / "extracted"
        parent = self.plugin_dir.parent
        staging = parent / f".plugin_store-self-update-staging-{operation_id}"
        displaced = parent / f".plugin_store-self-update-old-{operation_id}"
        ensure_within(work_dir, self.state_dir)
        ensure_within(staging, parent)
        ensure_within(displaced, parent)
        work_dir.mkdir(parents=True, exist_ok=False)

        try:
            request = urllib.request.Request(
                archive_url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/zip, application/octet-stream;q=0.9, */*;q=0.1",
                },
            )
            downloaded = 0
            try:
                with urllib.request.urlopen(request, timeout=30) as response, archive.open("wb") as fh:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > self.max_archive_bytes:
                            raise StoreError(
                                "Plugin Store update archive exceeds the configured download safety limit.",
                                413,
                            )
                        fh.write(chunk)
            except StoreError:
                raise
            except urllib.error.HTTPError as exc:
                raise StoreError(
                    f"GitHub returned HTTP {exc.code} while downloading the Plugin Store update.",
                    502,
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise StoreError(f"Plugin Store update download failed: {exc}", 502) from exc

            safe_extract_zip(
                archive,
                extracted,
                max_extract_bytes=self.max_extract_bytes,
                max_files=self.max_files,
            )
            source_root = locate_plugin_root(extracted)
            remote_manifest = read_plugin_manifest(source_root)
            validation = validate_plugin_manifest(
                remote_manifest,
                plugin_root=source_root,
                host_version=self.host_version,
            )
            if validation["id"] != "plugin_store":
                raise StoreError("Downloaded update is not the Plugin Store.", 422)
            if validation["version"] != expected_version:
                raise StoreError(
                    "Downloaded Plugin Store version does not match the version advertised by GitHub.",
                    409,
                )
            if not validation["compatible"]:
                raise StoreError(
                    f"Plugin Store update is not compatible with this feedBack Host: {validation['compatibility_reason']}",
                    409,
                )

            remote_homepage = str(remote_manifest.get("homepage") or "").strip()
            if parse_github_repo(remote_homepage) != parse_github_repo(homepage):
                raise StoreError("Downloaded Plugin Store repository identity changed unexpectedly.", 409)

            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source_root, staging, symlinks=False)

            # Keep the old directory until the replacement is in place. If the
            # second rename fails, restore it immediately.
            os.replace(self.plugin_dir, displaced)
            try:
                os.replace(staging, self.plugin_dir)
            except Exception:
                if displaced.exists() and not self.plugin_dir.exists():
                    os.replace(displaced, self.plugin_dir)
                raise

            shutil.rmtree(displaced, ignore_errors=True)
            atomic_write_json(
                self.state_dir / "self-update.json",
                {
                    "checked_at": time.time(),
                    "remote_version": expected_version,
                    "etag": None,
                    "last_modified": None,
                    "warning": None,
                },
            )

            self._log_info(
                "plugin_store_self_update_complete",
                extra={
                    "from_version": local_version,
                    "to_version": expected_version,
                    "repository": homepage,
                },
            )
            return {
                "ok": True,
                "operation": "self_update",
                "from_version": local_version,
                "version": expected_version,
                "restart_required": True,
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
            # A failed replacement should normally restore this above. Do not
            # delete a displaced old copy if the live directory is missing.
            if displaced.exists() and self.plugin_dir.exists():
                shutil.rmtree(displaced, ignore_errors=True)

    def _bundled_registry(self) -> dict[str, Any]:
        text = (self.plugin_dir / "registry.yaml").read_text(encoding="utf-8")
        return load_registry_yaml(text, official=True)

    def _read_cached_registry(self, path: Path, *, official: bool) -> dict[str, Any] | None:
        try:
            return load_registry_yaml(path.read_text(encoding="utf-8"), official=official)
        except (OSError, StoreError):
            return None

    def _fetch_registry(
        self,
        *,
        url: str | None,
        cache_path: Path,
        meta_path: Path,
        official: bool,
        force: bool,
        allow_bundled_fallback: bool,
        validate_third_party_plugins: bool = False,
        store_id: str | None = None,
    ) -> dict[str, Any]:
        if not url:
            if allow_bundled_fallback:
                return {
                    "registry": self._bundled_registry(),
                    "source": "bundled",
                    "source_url": None,
                    "changed": False,
                    "stale": False,
                    "warning": None,
                }
            raise StoreError("Third-party store has no registry URL.", 500)

        meta = read_json(meta_path, {}) or {}
        now = time.time()
        if (
            not force
            and cache_path.is_file()
            and now - float(meta.get("checked_at", 0)) < REGISTRY_CACHE_SECONDS
        ):
            cached = self._read_cached_registry(cache_path, official=official)
            if cached:
                return {
                    "registry": cached,
                    "source": "cache",
                    "source_url": url,
                    "changed": False,
                    "stale": False,
                    "warning": None,
                }

        headers = {"Accept": "text/yaml, text/plain;q=0.9, */*;q=0.1"}
        if meta.get("etag"):
            headers["If-None-Match"] = str(meta["etag"])
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = str(meta["last_modified"])

        try:
            text, response_headers, final_url = self._fetch_text(
                url,
                max_bytes=MAX_REGISTRY_BYTES,
                timeout=12,
                headers=headers,
            )
            registry = load_registry_yaml(text, official=official)
            if validate_third_party_plugins:
                assert store_id is not None
                compatibility = self._validate_third_party_registry_plugins(
                    store_id,
                    registry,
                )
                atomic_write_json(self._compat_path(store_id), compatibility)

            atomic_write_text(cache_path, text)
            atomic_write_json(
                meta_path,
                {
                    "checked_at": now,
                    "etag": response_headers.get("etag"),
                    "last_modified": response_headers.get("last_modified"),
                    "source_url": final_url,
                },
            )
            return {
                "registry": registry,
                "source": "remote",
                "source_url": url,
                "changed": True,
                "stale": False,
                "warning": None,
            }
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                meta["checked_at"] = now
                atomic_write_json(meta_path, meta)
                cached = self._read_cached_registry(cache_path, official=official)
                if cached:
                    return {
                        "registry": cached,
                        "source": "cache",
                        "source_url": url,
                        "changed": False,
                        "stale": False,
                        "warning": None,
                    }
            fetch_error = f"Registry request failed: HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            fetch_error = f"Registry request failed: {exc}"
        except StoreError as exc:
            fetch_error = exc.message

        cached = self._read_cached_registry(cache_path, official=official)
        if cached:
            return {
                "registry": cached,
                "source": "cache",
                "source_url": url,
                "changed": False,
                "stale": True,
                "warning": fetch_error,
            }
        if allow_bundled_fallback:
            return {
                "registry": self._bundled_registry(),
                "source": "bundled",
                "source_url": url,
                "changed": False,
                "stale": True,
                "warning": fetch_error,
            }
        raise StoreError(fetch_error, 502)

    # ---------- store persistence ----------

    def _configured_stores(self) -> list[dict[str, Any]]:
        data = read_json(self.stores_path, {"stores": []}) or {"stores": []}
        stores = data.get("stores", []) if isinstance(data, dict) else []
        if not isinstance(stores, list):
            return []
        return [item for item in stores if isinstance(item, dict)]

    def _save_stores(self, stores: list[dict[str, Any]]) -> None:
        atomic_write_json(self.stores_path, {"schema": 1, "stores": stores})

    def _managed(self) -> dict[str, Any]:
        data = read_json(self.managed_path, {}) or {}
        return data if isinstance(data, dict) else {}

    def _save_managed(self, data: dict[str, Any]) -> None:
        atomic_write_json(self.managed_path, data)

    def _exclusions(self) -> set[str]:
        data = read_json(self.exclusions_path, {"plugins": []}) or {"plugins": []}
        plugins = data.get("plugins", []) if isinstance(data, dict) else []
        if not isinstance(plugins, list):
            return set()
        return {str(item) for item in plugins if ID_RE.fullmatch(str(item))}

    def _save_exclusions(self, plugins: set[str]) -> None:
        atomic_write_json(
            self.exclusions_path,
            {"schema": 1, "plugins": sorted(plugins)},
        )

    def set_excluded(self, plugin_id: str, excluded: bool) -> dict[str, Any]:
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)
        if plugin_id == "plugin_store":
            raise StoreError("The Plugin Store cannot be excluded.", 409)

        _found, source = self._locate_installed(plugin_id)
        if source is None:
            raise StoreError("Only installed plugins can be excluded.", 404)

        exclusions = self._exclusions()
        if excluded:
            exclusions.add(plugin_id)
        else:
            exclusions.discard(plugin_id)
        self._save_exclusions(exclusions)
        return {
            "ok": True,
            "plugin_id": plugin_id,
            "excluded": plugin_id in exclusions,
        }

    def _fetch_repo_manifest(
        self,
        repository: str,
        ref: str,
        *,
        plugin_id_hint: str = "plugin",
    ) -> dict[str, Any]:
        entry = {
            "id": plugin_id_hint if ID_RE.fullmatch(plugin_id_hint) else "plugin",
            "repository": repository,
            "ref": ref,
        }
        url = raw_manifest_url(entry)
        try:
            text, _headers, _final = self._fetch_text(
                url,
                max_bytes=MAX_MANIFEST_BYTES,
                timeout=10,
                headers={"Accept": "application/json, text/plain;q=0.9"},
            )
        except urllib.error.HTTPError as exc:
            raise StoreError(
                f"Could not read plugin.json from {repository} at {ref}: HTTP {exc.code}.",
                422,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise StoreError(
                f"Could not read plugin.json from {repository} at {ref}: {exc}",
                422,
            ) from exc

        try:
            manifest = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError("Remote plugin.json is invalid JSON.", 422) from exc
        if not isinstance(manifest, dict):
            raise StoreError("Remote plugin.json must be a JSON object.", 422)
        return manifest

    def inspect_github_plugin(self, repository: str) -> dict[str, Any]:
        parsed = parse_https_github_repo(repository)
        if not parsed:
            raise StoreError(
                "Enter a public GitHub repository URL such as https://github.com/owner/repo.",
                400,
            )
        owner, repo = parsed
        normalized = f"https://github.com/{owner}/{repo}"

        last_error: StoreError | None = None
        for ref in ("main", "master"):
            try:
                manifest = self._fetch_repo_manifest(normalized, ref)
                compatibility = validate_plugin_manifest(
                    manifest,
                    expected_entry=None,
                    host_version=self.host_version,
                )
                plugin_id = compatibility["id"]
                if plugin_id == "plugin_store":
                    raise StoreError("The Plugin Store cannot install another copy of itself.", 409)

                # Avoid ambiguous duplicate ownership. Direct installs are for
                # plugins that are not already offered by a configured store.
                if plugin_id in self._official_ids():
                    raise StoreError(
                        f"{plugin_id} is already available from the official feedBack store.",
                        409,
                    )
                if plugin_id in self._other_third_party_ids():
                    raise StoreError(
                        f"{plugin_id} is already available from a configured third-party store.",
                        409,
                    )

                return {
                    "id": plugin_id,
                    "name": str(manifest.get("name") or plugin_id),
                    "description": str(manifest.get("description") or ""),
                    "version": compatibility["version"],
                    "repository": normalized,
                    "ref": ref,
                    "ref_kind": "head",
                    "compatible": compatibility["compatible"],
                    "compatibility_reason": compatibility["compatibility_reason"],
                    "min_host": compatibility["min_host"],
                }
            except StoreError as exc:
                last_error = exc
                # A manifest found on main/master but rejected for a semantic
                # reason should not be masked by probing the other branch.
                if exc.status_code in {400, 409}:
                    raise

        if last_error is not None:
            raise StoreError(
                "No compatible feedBack plugin.json was found at the repository root on main or master.",
                422,
            ) from last_error
        raise StoreError("Could not inspect the GitHub plugin repository.", 422)

    def _direct_managed(self) -> dict[str, dict[str, Any]]:
        managed = self._managed()
        return {
            plugin_id: info
            for plugin_id, info in managed.items()
            if isinstance(info, dict) and info.get("store_id") == DIRECT_STORE_ID
        }

    def _direct_entry(self, plugin_id: str, *, force: bool = False) -> dict[str, Any]:
        info = self._direct_managed().get(plugin_id)
        if not info:
            raise StoreError("Direct GitHub plugin is not managed by Plugin Store.", 404)

        repository = str(info.get("repository") or "")
        tracking_ref = str(info.get("tracking_ref") or info.get("ref") or "main")
        cache_path = self.direct_cache_dir / f"{plugin_id}.json"
        ensure_within(cache_path, self.direct_cache_dir)
        cached = read_json(cache_path, {}) or {}

        if (
            not force
            and isinstance(cached, dict)
            and time.time() - float(cached.get("checked_at") or 0) < REGISTRY_CACHE_SECONDS
            and isinstance(cached.get("entry"), dict)
        ):
            return cached["entry"]

        try:
            manifest = self._fetch_repo_manifest(
                repository,
                tracking_ref,
                plugin_id_hint=plugin_id,
            )
            compatibility = validate_plugin_manifest(
                manifest,
                expected_entry=None,
                host_version=self.host_version,
            )
            if compatibility["id"] != plugin_id:
                raise StoreError(
                    "Direct GitHub repository plugin id changed; automatic updates are blocked.",
                    409,
                )
            entry = {
                "id": plugin_id,
                "name": str(manifest.get("name") or plugin_id),
                "description": str(manifest.get("description") or ""),
                "version": compatibility["version"],
                "repository": repository,
                "ref": tracking_ref,
                "ref_kind": "head",
            }
            atomic_write_json(
                cache_path,
                {
                    "checked_at": time.time(),
                    "entry": entry,
                    "compatibility": compatibility,
                },
            )
            return entry
        except StoreError:
            if isinstance(cached, dict) and isinstance(cached.get("entry"), dict):
                return cached["entry"]
            target = safe_target(self.plugin_root, plugin_id)
            manifest = read_plugin_manifest(target)
            return {
                "id": plugin_id,
                "name": str(manifest.get("name") or plugin_id),
                "description": str(manifest.get("description") or ""),
                "version": str(manifest.get("version") or info.get("version") or "0.0.0"),
                "repository": repository,
                "ref": tracking_ref,
                "ref_kind": "head",
            }

    def _third_dir(self, store_id: str) -> Path:
        if not re.fullmatch(r"third-[0-9a-f]{12}", store_id):
            raise StoreError("Invalid third-party store id.", 400)
        path = self.third_cache_dir / store_id
        ensure_within(path, self.third_cache_dir)
        return path

    def _compat_path(self, store_id: str) -> Path:
        return self._third_dir(store_id) / "compatibility.json"

    def _third_registry_info(self, config: dict[str, Any], *, force: bool) -> dict[str, Any]:
        store_id = str(config["id"])
        directory = self._third_dir(store_id)
        directory.mkdir(parents=True, exist_ok=True)
        return self._fetch_registry(
            url=str(config["url"]),
            cache_path=directory / "registry.yaml",
            meta_path=directory / "meta.json",
            official=False,
            force=force,
            allow_bundled_fallback=False,
            validate_third_party_plugins=True,
            store_id=store_id,
        )

    def _official_info(self, *, force: bool) -> dict[str, Any]:
        return self._fetch_registry(
            url=self.registry_url,
            cache_path=self.official_cache_path,
            meta_path=self.official_meta_path,
            official=True,
            force=force,
            allow_bundled_fallback=True,
        )

    def _official_ids(self) -> set[str]:
        info = self._official_info(force=False)
        return {entry["id"] for entry in info["registry"]["plugins"]}

    def _other_third_party_ids(self, exclude_store_id: str | None = None) -> set[str]:
        ids: set[str] = set()
        for config in self._configured_stores():
            store_id = str(config.get("id", ""))
            if not store_id or store_id == exclude_store_id:
                continue
            cached = self._read_cached_registry(
                self._third_dir(store_id) / "registry.yaml",
                official=False,
            )
            if cached:
                ids.update(entry["id"] for entry in cached["plugins"])
        return ids

    def _fetch_remote_manifest(self, entry: dict[str, Any]) -> dict[str, Any]:
        url = raw_manifest_url(entry)
        try:
            text, _headers, _final = self._fetch_text(
                url,
                max_bytes=MAX_MANIFEST_BYTES,
                timeout=10,
                headers={"Accept": "application/json, text/plain;q=0.9"},
            )
        except urllib.error.HTTPError as exc:
            raise StoreError(
                f"Could not read plugin.json for {entry['id']}: HTTP {exc.code}.",
                422,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise StoreError(f"Could not read plugin.json for {entry['id']}: {exc}", 422) from exc
        try:
            manifest = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StoreError(f"Plugin {entry['id']} has invalid plugin.json JSON.", 422) from exc
        if not isinstance(manifest, dict):
            raise StoreError(f"Plugin {entry['id']} plugin.json is not a JSON object.", 422)
        return manifest

    def _validate_third_party_registry_plugins(
        self,
        store_id: str,
        registry: dict[str, Any],
    ) -> dict[str, Any]:
        official_ids = self._official_ids()
        other_ids = self._other_third_party_ids(exclude_store_id=store_id)
        result: dict[str, Any] = {}

        for entry in registry["plugins"]:
            plugin_id = entry["id"]
            if plugin_id == "plugin_store" or plugin_id in official_ids:
                raise StoreError(
                    f"Third-party store attempts to publish reserved official plugin id '{plugin_id}'.",
                    422,
                )
            if plugin_id in other_ids:
                raise StoreError(
                    f"Plugin id '{plugin_id}' is already provided by another configured third-party store.",
                    422,
                )

            manifest = self._fetch_remote_manifest(entry)
            compatibility = validate_plugin_manifest(
                manifest,
                expected_entry=entry,
                host_version=self.host_version,
            )
            result[plugin_id] = {
                **compatibility,
                "checked_at": time.time(),
            }

        return result

    def add_store(self, url: str, *, acknowledge_risk: bool) -> dict[str, Any]:
        if not acknowledge_risk:
            raise StoreError("You must acknowledge the third-party plugin risk before adding a store.", 400)

        normalized = normalize_registry_url(url)
        stores = self._configured_stores()
        if len(stores) >= MAX_THIRD_PARTY_STORES:
            raise StoreError(f"A maximum of {MAX_THIRD_PARTY_STORES} third-party stores is supported.", 409)
        if any(str(item.get("url")) == normalized for item in stores):
            raise StoreError("That third-party store is already configured.", 409)

        store_id = _store_id_for_url(normalized)
        directory = self._third_dir(store_id)
        directory.mkdir(parents=True, exist_ok=True)

        # First fetch is strict: invalid/incompatible registry structure is never
        # persisted as a configured store.
        try:
            text, response_headers, final_url = self._fetch_text(
                normalized,
                max_bytes=MAX_REGISTRY_BYTES,
                timeout=12,
                headers={"Accept": "text/yaml, text/plain;q=0.9, */*;q=0.1"},
            )
            registry = load_registry_yaml(text, official=False)
            compatibility = self._validate_third_party_registry_plugins(store_id, registry)
        except urllib.error.HTTPError as exc:
            raise StoreError(f"Third-party store returned HTTP {exc.code}.", 422) from exc
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise StoreError(f"Could not load third-party store: {exc}", 422) from exc

        atomic_write_text(directory / "registry.yaml", text)
        atomic_write_json(directory / "compatibility.json", compatibility)
        atomic_write_json(
            directory / "meta.json",
            {
                "checked_at": time.time(),
                "etag": response_headers.get("etag"),
                "last_modified": response_headers.get("last_modified"),
                "source_url": final_url,
            },
        )

        store_meta = registry["store"]
        config = {
            "id": store_id,
            "name": store_meta["name"],
            "description": store_meta.get("description") or "",
            "homepage": store_meta.get("homepage"),
            "url": normalized,
            "added_at": time.time(),
        }
        stores.append(config)
        self._save_stores(stores)
        self._log_info(
            "plugin_store_third_party_added",
            extra={"store_id": store_id, "url": normalized, "store_name": config["name"]},
        )
        return {"ok": True, "store": config}

    def remove_store(self, store_id: str) -> dict[str, Any]:
        stores = self._configured_stores()
        kept = [item for item in stores if str(item.get("id")) != store_id]
        if len(kept) == len(stores):
            raise StoreError("Third-party store not found.", 404)

        managed = self._managed()
        preserved = sorted(
            plugin_id
            for plugin_id, info in managed.items()
            if isinstance(info, dict)
            and info.get("store_id") == store_id
            and safe_target(self.plugin_root, plugin_id).exists()
        )

        self._save_stores(kept)
        directory = self._third_dir(store_id)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

        self._log_info(
            "plugin_store_third_party_removed",
            extra={"store_id": store_id, "preserved_plugins": preserved},
        )
        return {
            "ok": True,
            "store_id": store_id,
            "installed_plugins_preserved": preserved,
        }


    # ---------- rollback snapshots ----------

    def _backup_plugin_dir(self, plugin_id: str) -> Path:
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)
        path = (self.backups_dir / plugin_id).resolve(strict=False)
        ensure_within(path, self.backups_dir)
        return path

    def _backup_records(self, plugin_id: str) -> list[dict[str, Any]]:
        root = self._backup_plugin_dir(plugin_id)
        if not root.is_dir():
            return []

        records: list[dict[str, Any]] = []
        for child in root.iterdir():
            if not child.is_dir() or child.is_symlink():
                continue
            meta = read_json(child / "metadata.json", {}) or {}
            plugin_copy = child / "plugin"
            if not isinstance(meta, dict) or not plugin_copy.is_dir():
                continue
            try:
                manifest = read_plugin_manifest(plugin_copy)
            except StoreError:
                continue
            if manifest.get("id") != plugin_id:
                continue
            version = str(manifest.get("version") or meta.get("version") or "")
            records.append(
                {
                    "path": child,
                    "version": version or None,
                    "created_at": float(meta.get("created_at") or 0),
                    "managed": meta.get("managed"),
                }
            )

        records.sort(key=lambda item: item["created_at"], reverse=True)
        return records

    def _trim_backups(self, plugin_id: str) -> None:
        records = self._backup_records(plugin_id)
        for record in records[self.backups_per_plugin:]:
            shutil.rmtree(record["path"], ignore_errors=True)

        root = self._backup_plugin_dir(plugin_id)
        try:
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass

    def _snapshot_backup(
        self,
        plugin_id: str,
        target: Path,
        managed_entry: dict[str, Any] | None,
    ) -> Path:
        if not target.is_dir() or target.is_symlink():
            raise StoreError("Installed plugin cannot be safely backed up.", 409)

        manifest = read_plugin_manifest(target)
        if manifest.get("id") != plugin_id:
            raise StoreError(
                "Installed plugin manifest does not match its directory; rollback snapshot refused.",
                409,
            )

        version = str(manifest.get("version") or "unknown")
        backup_root = self._backup_plugin_dir(plugin_id)
        backup_root.mkdir(parents=True, exist_ok=True)

        stamp = int(time.time() * 1000)
        backup = backup_root / f"{stamp}-{uuid.uuid4().hex[:8]}"
        ensure_within(backup, self.backups_dir)
        backup.mkdir(parents=False, exist_ok=False)

        try:
            shutil.copytree(target, backup / "plugin", symlinks=False)
            atomic_write_json(
                backup / "metadata.json",
                {
                    "plugin_id": plugin_id,
                    "version": version,
                    "created_at": time.time(),
                    "managed": managed_entry if isinstance(managed_entry, dict) else None,
                },
            )
        except Exception:
            shutil.rmtree(backup, ignore_errors=True)
            raise

        self._trim_backups(plugin_id)
        return backup

    def _rollback_info(
        self,
        plugin_id: str,
        installed_version: str | None,
    ) -> dict[str, Any] | None:
        for record in self._backup_records(plugin_id):
            if record.get("version") and record.get("version") != installed_version:
                return {
                    "version": record.get("version"),
                    "created_at": record.get("created_at"),
                }
        return None

    def rollback(self, plugin_id: str) -> dict[str, Any]:
        if plugin_id == "plugin_store":
            raise StoreError("The Plugin Store cannot roll itself back.", 409)
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)

        target = safe_target(self.plugin_root, plugin_id)
        if not target.is_dir() or target.is_symlink():
            raise StoreError("Plugin must be installed before it can be rolled back.", 404)

        current_manifest = read_plugin_manifest(target)
        if current_manifest.get("id") != plugin_id:
            raise StoreError("Installed plugin manifest does not match its directory.", 409)
        current_version = str(current_manifest.get("version") or "")

        candidate = None
        for record in self._backup_records(plugin_id):
            if record.get("version") != current_version:
                candidate = record
                break
        if not candidate:
            raise StoreError("No previous plugin version is available for rollback.", 404)

        managed = self._managed()
        current_managed = managed.get(plugin_id)

        operation_id = uuid.uuid4().hex
        staging = self.plugin_root / f".plugin_store-rollback-staging-{operation_id}"
        displaced = self.plugin_root / f".plugin_store-rollback-current-{plugin_id}-{operation_id}"
        ensure_within(staging, self.plugin_root)
        ensure_within(displaced, self.plugin_root)

        source = candidate["path"] / "plugin"
        if not source.is_dir():
            raise StoreError("Rollback snapshot is incomplete.", 500)

        try:
            # Stage the version we are about to restore *before* snapshotting
            # the current version. With retention=1, the new snapshot may evict
            # the old backup directory, but staging remains intact.
            shutil.copytree(source, staging, symlinks=False)
            restored_manifest = read_plugin_manifest(staging)
            if restored_manifest.get("id") != plugin_id:
                raise StoreError("Rollback snapshot failed manifest validation.", 500)

            # Preserve the current state so the rollback itself is reversible.
            self._snapshot_backup(
                plugin_id,
                target,
                current_managed if isinstance(current_managed, dict) else None,
            )

            os.replace(target, displaced)
            try:
                os.replace(staging, target)
            except Exception:
                if displaced.exists() and not target.exists():
                    os.replace(displaced, target)
                raise

            shutil.rmtree(displaced, ignore_errors=True)

            previous_managed = candidate.get("managed")
            if isinstance(previous_managed, dict):
                managed[plugin_id] = previous_managed
            else:
                managed.pop(plugin_id, None)
            self._save_managed(managed)

            self._log_info(
                "plugin_store_rollback_complete",
                extra={
                    "plugin_id": plugin_id,
                    "from_version": current_version,
                    "to_version": candidate.get("version"),
                },
            )
            return {
                "ok": True,
                "plugin_id": plugin_id,
                "operation": "rollback",
                "from_version": current_version or None,
                "version": candidate.get("version"),
                "restart_required": True,
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if displaced.exists():
                shutil.rmtree(displaced, ignore_errors=True)
            self._trim_backups(plugin_id)

    # ---------- catalog ----------

    def _folder_index(self, root: Path | None) -> dict[str, Path]:
        if root is None:
            return {}
        try:
            stamp = root.stat().st_mtime_ns
        except OSError:
            return {}
        cached = self._folder_index_cache.get(root)
        if cached and cached[0] == stamp:
            return cached[1]
        index = index_plugin_folders(root)
        self._folder_index_cache[root] = (stamp, index)
        return index

    def _locate_installed(self, plugin_id: str) -> tuple[Path, str | None]:
        """Find where a plugin is installed, however it got there.

        Returns (folder, source) where source is:
        - "store": ``plugin_root/<id>``, the folder this store manages;
        - "manual": another folder in plugin_root whose manifest has this id
          (for example a git clone named after its repository);
        - "builtin": the copy bundled with feedBack;
        - None: not installed (folder is where an install would go).
        """
        target = safe_target(self.plugin_root, plugin_id)
        if target.exists() or target.is_symlink():
            return target, "store"
        manual = self._folder_index(self.plugin_root).get(plugin_id)
        if manual is not None:
            return manual, "manual"
        builtin = self._folder_index(self.builtin_root).get(plugin_id)
        if builtin is not None:
            return builtin, "builtin"
        return target, None

    def _builtin_locked(self, plugin_id: str) -> bool:
        builtin = self._folder_index(self.builtin_root).get(plugin_id)
        return bool(
            builtin is not None
            and self.builtin_root is not None
            and builtin_copy_is_locked(builtin, self.builtin_root, plugin_id)
        )

    def _installed_state(
        self,
        entry: dict[str, Any],
        *,
        store_id: str,
        third_party: bool,
        compatibility: dict[str, Any] | None,
    ) -> dict[str, Any]:
        target, source = self._locate_installed(entry["id"])
        managed = self._managed().get(entry["id"])
        managed_by_store = bool(
            isinstance(managed, dict)
            and managed.get("store_id") == store_id
            and managed.get("repository") == entry["repository"]
        )

        state: dict[str, Any] = {
            **entry,
            "store_id": store_id,
            "third_party": third_party,
            "installed": False,
            "installed_version": None,
            "status": "available",
            "update_available": False,
            "has_settings": False,
            "has_screen": False,
            "nav_screen": None,
            "managed_by_store": managed_by_store,
            "compatible": True,
            "compatibility_reason": None,
            "min_host": None,
            "can_install": True,
            "can_update": False,
            "can_remove": False,
            "rollback_available": False,
            "rollback_version": None,
            "rollback_created_at": None,
            "excluded": entry["id"] in self._exclusions(),
            "install_source": source,
            "install_path": str(target) if source else None,
        }

        if compatibility:
            state["compatible"] = bool(compatibility.get("compatible", False))
            state["compatibility_reason"] = compatibility.get("compatibility_reason")
            state["min_host"] = compatibility.get("min_host")
        if not state["compatible"]:
            state["status"] = "incompatible"
            state["can_install"] = False

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
                state["can_install"] = False

                settings = manifest.get("settings")
                state["has_settings"] = bool(
                    isinstance(settings, dict)
                    and isinstance(settings.get("html"), str)
                    and settings.get("html").strip()
                )
                nav = manifest.get("nav")
                nav_screen = None
                if isinstance(nav, dict):
                    candidate = nav.get("screen")
                    if isinstance(candidate, str) and candidate.strip():
                        nav_screen = candidate.strip()
                screen_file = manifest.get("screen")
                state["nav_screen"] = nav_screen
                state["has_screen"] = bool(
                    nav_screen or (isinstance(screen_file, str) and screen_file.strip())
                )

                comparison = compare_versions(installed_version, entry["version"])
                state["catalog_newer"] = comparison < 0
                if source == "manual":
                    # Installed by hand (usually a git clone) under another
                    # folder name. Replacing it would leave two copies with the
                    # same id, and removing it would delete the user's checkout.
                    state["status"] = "installed_external"
                    state["can_update"] = False
                    state["can_remove"] = False
                elif source == "builtin":
                    # Ships with feedBack. It cannot be removed from here, but a
                    # newer catalog version can be installed into the user
                    # plugins folder, which the host loads first, unless the
                    # host always keeps its bundled copy.
                    locked = self._builtin_locked(entry["id"])
                    state["builtin_locked"] = locked
                    state["can_remove"] = False
                    if comparison < 0 and not locked and not third_party:
                        state["status"] = "update_available" if state["compatible"] else "incompatible"
                        state["update_available"] = True
                        state["can_update"] = bool(state["compatible"])
                    elif comparison > 0:
                        state["status"] = "local_newer"
                    else:
                        state["status"] = "installed"
                elif third_party and not managed_by_store:
                    state["status"] = "installed_external"
                    state["can_update"] = False
                    state["can_remove"] = False
                elif comparison < 0:
                    state["status"] = "update_available" if state["compatible"] else "incompatible"
                    state["update_available"] = True
                    state["can_update"] = bool(state["compatible"])
                    state["can_remove"] = True
                elif comparison > 0:
                    state["status"] = "local_newer"
                    state["can_remove"] = True
                else:
                    state["status"] = "installed"
                    state["can_remove"] = True
            except StoreError as exc:
                state["installed"] = True
                state["status"] = "broken"
                state["error"] = exc.message
                state["can_install"] = False
                state["can_update"] = bool(
                    state["compatible"]
                    and source == "store"
                    and (not third_party or managed_by_store)
                )
                state["can_remove"] = bool(
                    source == "store" and (not third_party or managed_by_store)
                )

        if source == "store":
            rollback = self._rollback_info(entry["id"], state.get("installed_version"))
            if rollback:
                state["rollback_available"] = True
                state["rollback_version"] = rollback.get("version")
                state["rollback_created_at"] = rollback.get("created_at")

        return state

    def catalog(self, force_refresh: bool = False) -> dict[str, Any]:
        official_info = self._official_info(force=force_refresh)
        stores_output: list[dict[str, Any]] = []

        official_plugins = [
            self._installed_state(
                entry,
                store_id=OFFICIAL_STORE_ID,
                third_party=False,
                compatibility=None,
            )
            for entry in official_info["registry"]["plugins"]
        ]
        stores_output.append(
            {
                "id": OFFICIAL_STORE_ID,
                "name": OFFICIAL_STORE_NAME,
                "description": "Curated plugins from the official got-feedBack GitHub organization.",
                "official": True,
                "third_party": False,
                "url": official_info["source_url"],
                "source": official_info["source"],
                "stale": official_info["stale"],
                "warning": official_info["warning"],
                "plugins": official_plugins,
            }
        )

        for config in self._configured_stores():
            store_id = str(config.get("id", ""))
            try:
                info = self._third_registry_info(config, force=force_refresh)
                compat = read_json(self._compat_path(store_id), {}) or {}
                plugins = [
                    self._installed_state(
                        entry,
                        store_id=store_id,
                        third_party=True,
                        compatibility=compat.get(entry["id"]) if isinstance(compat, dict) else None,
                    )
                    for entry in info["registry"]["plugins"]
                ]
                remote_meta = info["registry"].get("store") or {}
                stores_output.append(
                    {
                        "id": store_id,
                        "name": remote_meta.get("name") or config.get("name") or "Third-Party Store",
                        "description": remote_meta.get("description") or config.get("description") or "",
                        "homepage": remote_meta.get("homepage") or config.get("homepage"),
                        "official": False,
                        "third_party": True,
                        "url": config.get("url"),
                        "source": info["source"],
                        "stale": info["stale"],
                        "warning": info["warning"],
                        "plugins": plugins,
                    }
                )
            except StoreError as exc:
                stores_output.append(
                    {
                        "id": store_id,
                        "name": config.get("name") or "Third-Party Store",
                        "description": config.get("description") or "",
                        "homepage": config.get("homepage"),
                        "official": False,
                        "third_party": True,
                        "url": config.get("url"),
                        "source": "error",
                        "stale": True,
                        "warning": exc.message,
                        "plugins": [],
                    }
                )

        direct_managed = self._direct_managed()
        if direct_managed:
            direct_plugins: list[dict[str, Any]] = []
            direct_warning: str | None = None
            for plugin_id in sorted(direct_managed):
                try:
                    entry = self._direct_entry(plugin_id, force=force_refresh)
                    cached = read_json(self.direct_cache_dir / f"{plugin_id}.json", {}) or {}
                    compat = cached.get("compatibility") if isinstance(cached, dict) else None
                    direct_plugins.append(
                        self._installed_state(
                            entry,
                            store_id=DIRECT_STORE_ID,
                            third_party=True,
                            compatibility=compat if isinstance(compat, dict) else None,
                        )
                    )
                except StoreError as exc:
                    direct_warning = exc.message
                    info = direct_managed[plugin_id]
                    target = safe_target(self.plugin_root, plugin_id)
                    try:
                        manifest = read_plugin_manifest(target)
                        fallback_entry = {
                            "id": plugin_id,
                            "name": str(manifest.get("name") or plugin_id),
                            "description": str(manifest.get("description") or ""),
                            "version": str(manifest.get("version") or info.get("version") or "0.0.0"),
                            "repository": str(info.get("repository") or ""),
                            "ref": str(info.get("tracking_ref") or info.get("ref") or "main"),
                            "ref_kind": "head",
                        }
                        state = self._installed_state(
                            fallback_entry,
                            store_id=DIRECT_STORE_ID,
                            third_party=True,
                            compatibility={
                                "compatible": True,
                                "compatibility_reason": None,
                                "min_host": manifest.get("minHost"),
                            },
                        )
                        state["error"] = exc.message
                        direct_plugins.append(state)
                    except StoreError:
                        continue

            stores_output.append(
                {
                    "id": DIRECT_STORE_ID,
                    "name": DIRECT_STORE_NAME,
                    "description": "Plugins installed directly from public GitHub repositories.",
                    "official": False,
                    "third_party": True,
                    "direct": True,
                    "url": None,
                    "source": "github",
                    "stale": bool(direct_warning),
                    "warning": direct_warning,
                    "plugins": direct_plugins,
                }
            )

        return {
            "schema": 2,
            "stores": stores_output,
            # Compatibility alias for older v0.1.x frontend/tests.
            "plugins": official_plugins,
            "registry": {
                "source": official_info["source"],
                "source_url": official_info["source_url"],
                "changed": official_info["changed"],
                "stale": official_info["stale"],
                "warning": official_info["warning"],
            },
            "plugin_root": str(self.plugin_root),
            "host_version": self.host_version,
            "plugin_spec_major": PLUGIN_SPEC_MAJOR,
        }

    # ---------- plugin lifecycle ----------

    def _store_source(self, store_id: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
        if store_id == OFFICIAL_STORE_ID:
            info = self._official_info(force=False)
            return info["registry"], {
                "id": OFFICIAL_STORE_ID,
                "name": OFFICIAL_STORE_NAME,
                "url": info["source_url"],
            }, False

        if store_id == DIRECT_STORE_ID:
            plugins = [
                self._direct_entry(plugin_id, force=False)
                for plugin_id in sorted(self._direct_managed())
            ]
            return {
                "schema": STORE_SCHEMA,
                "store": {
                    "name": DIRECT_STORE_NAME,
                    "description": "Plugins installed directly from public GitHub repositories.",
                },
                "plugins": plugins,
            }, {
                "id": DIRECT_STORE_ID,
                "name": DIRECT_STORE_NAME,
                "url": None,
            }, True

        for config in self._configured_stores():
            if str(config.get("id")) == store_id:
                info = self._third_registry_info(config, force=False)
                return info["registry"], config, True
        raise StoreError("Plugin store source not found.", 404)

    def _entry(self, plugin_id: str, store_id: str = OFFICIAL_STORE_ID) -> tuple[dict[str, Any], dict[str, Any], bool]:
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)
        registry, config, third_party = self._store_source(store_id)
        for entry in registry["plugins"]:
            if entry["id"] == plugin_id:
                return entry, config, third_party
        raise StoreError("Plugin is not present in the selected store.", 404)

    def _download(self, entry: dict[str, Any], destination: Path, *, official: bool = False) -> None:
        url = archive_url(entry, official=official)
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

    def _install_entry(
        self,
        entry: dict[str, Any],
        *,
        replace: bool,
        store_id: str,
        store_config: dict[str, Any],
        third_party: bool,
        acknowledge_third_party: bool = False,
        managed_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plugin_id = str(entry.get("id") or "")
        if plugin_id == "plugin_store":
            raise StoreError("The Plugin Store cannot modify itself.", 409)
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)

        if third_party and not acknowledge_third_party:
            raise StoreError(
                "Third-party plugins execute code inside feedBack. Explicit risk acknowledgement is required.",
                400,
            )

        target = safe_target(self.plugin_root, plugin_id)
        if not target.exists() and not target.is_symlink():
            found, source = self._locate_installed(plugin_id)
            if source == "manual":
                raise StoreError(
                    f"Plugin is already installed manually at {found}. "
                    "Update or remove that copy yourself.",
                    409,
                )
            if source == "builtin":
                if self._builtin_locked(plugin_id):
                    raise StoreError(
                        "This plugin is bundled with feedBack and the host always "
                        "uses the bundled copy.",
                        409,
                    )
                # Updating a bundled plugin = a fresh install into the user
                # plugins folder, which the host loads ahead of the bundled one.
                replace = False
        if target.exists() and not replace:
            raise StoreError("Plugin is already installed.", 409)
        if not target.exists() and replace:
            raise StoreError("Plugin is not installed.", 404)
        if target.is_symlink():
            raise StoreError("Refusing to replace a symlinked plugin directory.", 409)

        managed = self._managed()
        if third_party and replace:
            origin = managed.get(plugin_id)
            if not isinstance(origin, dict) or origin.get("store_id") != store_id:
                raise StoreError(
                    "This installed plugin was not installed by this source; automatic replacement is blocked.",
                    409,
                )
            if origin.get("repository") != entry["repository"]:
                raise StoreError(
                    "Plugin repository changed; automatic replacement is blocked.",
                    409,
                )

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

        rollback_snapshot: Path | None = None
        update_committed = False
        try:
            self._download(entry, archive, official=store_id == OFFICIAL_STORE_ID)
            safe_extract_zip(
                archive,
                extracted,
                max_extract_bytes=self.max_extract_bytes,
                max_files=self.max_files,
            )
            source_root = locate_plugin_root(extracted)
            manifest = read_plugin_manifest(source_root)
            compatibility = validate_plugin_manifest(
                manifest,
                expected_entry=entry,
                plugin_root=source_root,
                host_version=self.host_version,
            )
            if not compatibility["compatible"]:
                raise StoreError(
                    f"Plugin is not compatible with this feedBack Host: {compatibility['compatibility_reason']}",
                    409,
                )

            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(source_root, staging, symlinks=False)

            if replace:
                current_managed = managed.get(plugin_id)
                rollback_snapshot = self._snapshot_backup(
                    plugin_id,
                    target,
                    current_managed if isinstance(current_managed, dict) else None,
                )
                os.replace(target, backup)
            try:
                os.replace(staging, target)
            except Exception:
                if replace and backup.exists() and not target.exists():
                    os.replace(backup, target)
                raise
            if backup.exists():
                shutil.rmtree(backup)

            managed_record = {
                "store_id": store_id,
                "store_name": store_config.get("name"),
                "repository": entry["repository"],
                "version": compatibility["version"],
                "ref": entry.get("ref"),
                "ref_kind": entry.get("ref_kind") or "head",
                "third_party": third_party,
                "installed_at": time.time(),
            }
            if managed_extra:
                managed_record.update(managed_extra)
            managed[plugin_id] = managed_record
            self._save_managed(managed)
            update_committed = True

            self._log_info(
                "plugin_store_install_complete",
                extra={
                    "plugin_id": plugin_id,
                    "version": compatibility["version"],
                    "replace": replace,
                    "store_id": store_id,
                    "third_party": third_party,
                    "ref": entry.get("ref"),
                    "ref_kind": entry.get("ref_kind") or "head",
                },
            )
            return {
                "ok": True,
                "plugin_id": plugin_id,
                "version": compatibility["version"],
                "operation": "update" if replace else "install",
                "restart_required": True,
                "third_party": third_party,
                "ref": entry.get("ref"),
            }
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
            if replace and rollback_snapshot is not None and not update_committed:
                shutil.rmtree(rollback_snapshot, ignore_errors=True)
                self._trim_backups(plugin_id)

    def install(
        self,
        plugin_id: str,
        *,
        replace: bool,
        store_id: str = OFFICIAL_STORE_ID,
        acknowledge_third_party: bool = False,
    ) -> dict[str, Any]:
        entry, store_config, third_party = self._entry(plugin_id, store_id)

        if third_party and store_id != DIRECT_STORE_ID:
            compat = read_json(self._compat_path(store_id), {}) or {}
            plugin_compat = compat.get(plugin_id) if isinstance(compat, dict) else None
            if not isinstance(plugin_compat, dict) or not plugin_compat.get("compatible", False):
                reason = (
                    plugin_compat.get("compatibility_reason")
                    if isinstance(plugin_compat, dict)
                    else "Compatibility has not been verified."
                )
                raise StoreError(
                    f"Plugin is not compatible with this feedBack Host: {reason}",
                    409,
                )

        managed_extra = None
        if store_id == DIRECT_STORE_ID:
            origin = self._managed().get(plugin_id)
            tracking_ref = (
                str(origin.get("tracking_ref") or entry.get("ref") or "main")
                if isinstance(origin, dict)
                else str(entry.get("ref") or "main")
            )
            managed_extra = {
                "direct": True,
                "tracking_ref": tracking_ref,
            }

        return self._install_entry(
            entry,
            replace=replace,
            store_id=store_id,
            store_config=store_config,
            third_party=third_party,
            acknowledge_third_party=acknowledge_third_party,
            managed_extra=managed_extra,
        )

    def install_from_github(
        self,
        repository: str,
        *,
        acknowledge_third_party: bool,
    ) -> dict[str, Any]:
        if not acknowledge_third_party:
            raise StoreError(
                "Direct GitHub plugins are third-party code. Explicit risk acknowledgement is required.",
                400,
            )

        entry = self.inspect_github_plugin(repository)
        if not entry["compatible"]:
            raise StoreError(
                f"Plugin is not compatible with this feedBack Host: {entry['compatibility_reason']}",
                409,
            )

        return self._install_entry(
            entry,
            replace=False,
            store_id=DIRECT_STORE_ID,
            store_config={
                "id": DIRECT_STORE_ID,
                "name": DIRECT_STORE_NAME,
                "url": entry["repository"],
            },
            third_party=True,
            acknowledge_third_party=True,
            managed_extra={
                "direct": True,
                "tracking_ref": entry["ref"],
            },
        )

    def check_plugin(self, store_id: str, plugin_id: str) -> dict[str, Any]:
        if not ID_RE.fullmatch(plugin_id):
            raise StoreError("Invalid plugin id.", 400)

        if store_id == OFFICIAL_STORE_ID:
            info = self._official_info(force=True)
            for entry in info["registry"]["plugins"]:
                if entry["id"] == plugin_id:
                    return self._installed_state(
                        entry,
                        store_id=OFFICIAL_STORE_ID,
                        third_party=False,
                        compatibility=None,
                    )
            raise StoreError("Plugin is not present in the official store.", 404)

        if store_id == DIRECT_STORE_ID:
            entry = self._direct_entry(plugin_id, force=True)
            cached = read_json(self.direct_cache_dir / f"{plugin_id}.json", {}) or {}
            compat = cached.get("compatibility") if isinstance(cached, dict) else None
            return self._installed_state(
                entry,
                store_id=DIRECT_STORE_ID,
                third_party=True,
                compatibility=compat if isinstance(compat, dict) else None,
            )

        for config in self._configured_stores():
            if str(config.get("id")) != store_id:
                continue
            info = self._third_registry_info(config, force=True)
            compat = read_json(self._compat_path(store_id), {}) or {}
            for entry in info["registry"]["plugins"]:
                if entry["id"] == plugin_id:
                    return self._installed_state(
                        entry,
                        store_id=store_id,
                        third_party=True,
                        compatibility=compat.get(plugin_id) if isinstance(compat, dict) else None,
                    )
            raise StoreError("Plugin is not present in the selected store.", 404)

        raise StoreError("Plugin store source not found.", 404)

    def _github_tags(self, repository: str, limit: int = 12) -> list[str]:
        parsed = parse_https_github_repo(repository)
        if not parsed:
            raise StoreError("Invalid GitHub repository.", 400)
        owner, repo = parsed
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repo, safe="")
        url = f"https://api.github.com/repos/{owner_q}/{repo_q}/tags?per_page={max(1, min(limit, 30))}"
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                final = urllib.parse.urlparse(response.geturl())
                if final.scheme != "https" or (final.hostname or "").lower() != "api.github.com":
                    raise StoreError("GitHub API redirected to an unexpected host.", 502)
                payload = response.read(512 * 1024 + 1)
                if len(payload) > 512 * 1024:
                    raise StoreError("GitHub tag response was unexpectedly large.", 502)
        except StoreError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 403:
                raise StoreError(
                    "GitHub rate limit reached while loading plugin versions. Try again later.",
                    429,
                ) from exc
            raise StoreError(
                f"GitHub returned HTTP {exc.code} while loading plugin versions.",
                502,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StoreError(f"Could not load plugin versions from GitHub: {exc}", 502) from exc

        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise StoreError("GitHub returned invalid tag data.", 502) from exc
        if not isinstance(data, list):
            raise StoreError("GitHub returned invalid tag data.", 502)

        tags: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            try:
                _validate_ref(name, "plugin")
            except StoreError:
                continue
            if name and name not in tags:
                tags.append(name)
        return tags[:limit]

    def versions(self, store_id: str, plugin_id: str) -> dict[str, Any]:
        entry, _store_config, third_party = self._entry(plugin_id, store_id)
        repository = entry["repository"]

        versions: list[dict[str, Any]] = [
            {
                "version": entry["version"],
                "ref": entry["ref"],
                "ref_kind": entry.get("ref_kind") or "head",
                "label": "Latest",
                "compatible": True,
                "compatibility_reason": None,
            }
        ]
        seen = {(entry["version"], entry["ref"])}

        for tag in self._github_tags(repository):
            try:
                manifest = self._fetch_repo_manifest(
                    repository,
                    tag,
                    plugin_id_hint=plugin_id,
                )
                compatibility = validate_plugin_manifest(
                    manifest,
                    expected_entry=None,
                    host_version=self.host_version,
                )
                if compatibility["id"] != plugin_id:
                    continue
                key = (compatibility["version"], tag)
                if key in seen:
                    continue
                seen.add(key)
                versions.append(
                    {
                        "version": compatibility["version"],
                        "ref": tag,
                        "ref_kind": "tag",
                        "label": tag,
                        "compatible": compatibility["compatible"],
                        "compatibility_reason": compatibility["compatibility_reason"],
                    }
                )
            except StoreError:
                continue

        return {
            "plugin_id": plugin_id,
            "repository": repository,
            "store_id": store_id,
            "third_party": third_party,
            "versions": versions,
        }

    def install_version(
        self,
        store_id: str,
        plugin_id: str,
        ref: str,
        ref_kind: str,
        *,
        acknowledge_third_party: bool,
    ) -> dict[str, Any]:
        base_entry, store_config, third_party = self._entry(plugin_id, store_id)
        listing = self.versions(store_id, plugin_id)

        selected = None
        for item in listing["versions"]:
            if item.get("ref") == ref and item.get("ref_kind") == ref_kind:
                selected = item
                break
        if not selected:
            raise StoreError("Requested plugin version is not in the verified version list.", 404)
        if selected.get("compatible") is False:
            raise StoreError(
                f"Plugin version is incompatible: {selected.get('compatibility_reason') or 'unknown reason'}",
                409,
            )

        entry = {
            **base_entry,
            "version": selected["version"],
            "ref": selected["ref"],
            "ref_kind": selected["ref_kind"],
        }

        target = safe_target(self.plugin_root, plugin_id)
        replace = target.exists()

        managed_extra = {
            "selected_ref": selected["ref"],
            "selected_ref_kind": selected["ref_kind"],
        }
        if store_id == DIRECT_STORE_ID:
            origin = self._managed().get(plugin_id)
            managed_extra.update(
                {
                    "direct": True,
                    "tracking_ref": (
                        str(origin.get("tracking_ref") or base_entry.get("ref") or "main")
                        if isinstance(origin, dict)
                        else str(base_entry.get("ref") or "main")
                    ),
                }
            )

        return self._install_entry(
            entry,
            replace=replace,
            store_id=store_id,
            store_config=store_config,
            third_party=third_party,
            acknowledge_third_party=acknowledge_third_party,
            managed_extra=managed_extra,
        )

    def update_all(self, *, acknowledge_third_party: bool = False) -> dict[str, Any]:
        catalog = self.catalog(force_refresh=False)
        candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for store in catalog.get("stores", []):
            if not isinstance(store, dict):
                continue
            for plugin in store.get("plugins", []):
                if (
                    isinstance(plugin, dict)
                    and plugin.get("status") == "update_available"
                    and plugin.get("can_update") is True
                    and plugin.get("excluded") is not True
                ):
                    candidates.append((store, plugin))

        third_party_count = sum(
            1 for store, _plugin in candidates if store.get("third_party") is True
        )
        if third_party_count and not acknowledge_third_party:
            raise StoreError(
                "Update All includes third-party plugins. Explicit risk acknowledgement is required.",
                400,
            )

        updated: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for store, plugin in candidates:
            try:
                result = self.install(
                    str(plugin["id"]),
                    replace=True,
                    store_id=str(store["id"]),
                    acknowledge_third_party=store.get("third_party") is True,
                )
                updated.append(
                    {
                        "plugin_id": plugin["id"],
                        "name": plugin.get("name"),
                        "store_id": store["id"],
                        "store_name": store.get("name"),
                        "version": result.get("version"),
                    }
                )
            except StoreError as exc:
                failed.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "name": plugin.get("name"),
                        "store_id": store.get("id"),
                        "store_name": store.get("name"),
                        "error": exc.message,
                    }
                )
            except Exception as exc:
                self.log.exception(
                    "plugin_store_update_all_unexpected_failure",
                    extra={
                        "plugin_id": plugin.get("id"),
                        "store_id": store.get("id"),
                    },
                )
                failed.append(
                    {
                        "plugin_id": plugin.get("id"),
                        "name": plugin.get("name"),
                        "store_id": store.get("id"),
                        "store_name": store.get("name"),
                        "error": str(exc),
                    }
                )

        return {
            "ok": len(failed) == 0,
            "operation": "update_all",
            "candidates": len(candidates),
            "updated_count": len(updated),
            "failed_count": len(failed),
            "third_party_count": third_party_count,
            "updated": updated,
            "failed": failed,
            "restart_required": bool(updated),
        }

    def remove(self, plugin_id: str, store_id: str = OFFICIAL_STORE_ID) -> dict[str, Any]:
        if plugin_id == "plugin_store":
            raise StoreError("The Plugin Store cannot remove itself.", 409)

        _entry, _config, third_party = self._entry(plugin_id, store_id)
        target = safe_target(self.plugin_root, plugin_id)
        if not target.exists():
            found, source = self._locate_installed(plugin_id)
            if source == "builtin":
                raise StoreError("This plugin is bundled with feedBack and cannot be removed here.", 409)
            if source == "manual":
                raise StoreError(
                    f"This plugin was installed manually at {found}; remove that folder yourself.",
                    409,
                )
            raise StoreError("Plugin is not installed.", 404)
        if target.is_symlink():
            raise StoreError("Refusing to remove a symlinked plugin directory.", 409)

        managed = self._managed()
        if third_party:
            origin = managed.get(plugin_id)
            if not isinstance(origin, dict) or origin.get("store_id") != store_id:
                raise StoreError(
                    "This plugin was not installed by this third-party store; automatic removal is blocked.",
                    409,
                )

        manifest = read_plugin_manifest(target)
        if manifest.get("id") != plugin_id:
            raise StoreError(
                "Installed plugin manifest does not match the requested plugin id.",
                409,
            )

        shutil.rmtree(target)
        managed.pop(plugin_id, None)
        self._save_managed(managed)

        exclusions = self._exclusions()
        if plugin_id in exclusions:
            exclusions.discard(plugin_id)
            self._save_exclusions(exclusions)

        direct_cache = self.direct_cache_dir / f"{plugin_id}.json"
        ensure_within(direct_cache, self.direct_cache_dir)
        try:
            direct_cache.unlink()
        except FileNotFoundError:
            pass

        self._log_info(
            "plugin_store_remove_complete",
            extra={"plugin_id": plugin_id, "store_id": store_id},
        )
        return {
            "ok": True,
            "plugin_id": plugin_id,
            "operation": "remove",
            "restart_required": True,
        }
