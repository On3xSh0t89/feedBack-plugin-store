\
#!/usr/bin/env python3
"""Regenerate registry.yaml from official got-feedBack plugin repositories.

Rules:
- enumerate public repositories in the got-feedBack GitHub organization;
- consider repository names beginning with "feedback-plugin-" (case-insensitive);
- skip feedBack-plugin-spec (documentation, not a runtime plugin);
- require a root plugin.json on the repository's default branch;
- skip manifests with private=true;
- use plugin.json for id/name/version/description;
- write only repositories owned by got-feedBack.

Set GITHUB_TOKEN to raise GitHub API rate limits when needed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import yaml

ORG = "got-feedBack"
API = "https://api.github.com"
USER_AGENT = "feedBack-plugin-store-registry-sync/0.1.0"


def request_json(url: str):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def repo_list():
    page = 1
    repos = []
    while True:
        batch = request_json(f"{API}/orgs/{quote(ORG)}/repos?type=public&per_page=100&page={page}")
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return repos


def manifest_for(repo: dict):
    name = repo["name"]
    branch = repo.get("default_branch") or "main"
    raw = (
        "https://raw.githubusercontent.com/"
        f"{quote(ORG)}/{quote(name)}/{quote(branch, safe='')}/plugin.json"
    )
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        with urlopen(Request(raw, headers=headers), timeout=20) as response:
            return json.load(response), branch
    except HTTPError as exc:
        if exc.code == 404:
            return None, branch
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[1] / "registry.yaml"),
        help="registry file to write",
    )
    args = parser.parse_args()

    entries = []
    skipped = []

    try:
        repos = repo_list()
        for repo in repos:
            name = str(repo.get("name") or "")
            if not name.lower().startswith("feedback-plugin-"):
                continue
            if name.lower() == "feedback-plugin-spec":
                continue
            if repo.get("fork"):
                # The catalog is intentionally first-party, not a mirror of forks.
                skipped.append((name, "fork"))
                continue

            manifest, branch = manifest_for(repo)
            if not manifest:
                skipped.append((name, "no plugin.json"))
                continue
            if manifest.get("private") is True:
                skipped.append((name, "manifest private=true"))
                continue

            pid = manifest.get("id")
            pname = manifest.get("name")
            version = manifest.get("version")
            description = manifest.get("description") or repo.get("description") or ""
            if not all(isinstance(x, str) and x.strip() for x in (pid, pname, version)):
                skipped.append((name, "missing id/name/version"))
                continue

            entries.append({
                "id": pid.strip(),
                "name": pname.strip(),
                "description": str(description).strip(),
                "version": version.strip(),
                "repository": f"https://github.com/{ORG}/{name}",
                "ref": branch,
            })
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1

    entries.sort(key=lambda item: item["name"].casefold())
    output = Path(args.output)
    payload = {
        "schema": 1,
        "source": f"https://github.com/orgs/{ORG}/repositories",
        "plugins": entries,
    }
    header = (
        "# Generated from public plugin repositories in the official got-feedBack GitHub organization.\n"
        "# Refresh with: python tools/sync_registry.py\n\n"
    )
    output.write_text(
        header + yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )

    print(f"wrote {len(entries)} plugins to {output}")
    for name, reason in sorted(skipped):
        print(f"skipped {name}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
