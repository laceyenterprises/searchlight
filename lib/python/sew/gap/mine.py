"""PyPI/GitHub candidate mining, with injectable recorded source readers.

Mining proposes source-backed jobs; it never authors a fixture or calls a model.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from ..schema import SchemaError
from .catalog import _hash, _url

DEFAULT_PACKAGES = ("requests", "urllib3", "httpx", "pydantic", "pandas")
MARKERS = {
    "breaking": (5, r"\bbreaking\b"),
    "removed": (4, r"\bremov(?:ed|al)\b"),
    "renamed": (4, r"\brenam(?:ed|ing)\b"),
    "default-change": (
        3,
        r"\bdefault\b[^\n.]{0,120}\b(?:chang\w*|now|switch\w*|flip\w*)\b|\b(?:chang\w*|switch\w*|flip\w*)\b[^\n.]{0,120}\bdefault\b",
    ),
    "security": (5, r"\b(?:security|vulnerabilit\w*|CVE-\d{4}-\d+)\b"),
}


def read_url(url):
    """Read public metadata only, with a timeout and bounded response size."""
    _url(url, "source URL")
    request = Request(
        url, headers={"User-Agent": "agent-os-sew-gap/1", "Accept": "application/json"}
    )
    if urlsplit(url).netloc.lower() in {"api.github.com", "api.github.com:443"}:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token:
            # urllib drops unredirected headers when following redirects, so
            # credentials never accompany redirected release-note requests.
            request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise SchemaError(f"source exceeds metadata cap: {url}")
        return data.decode("utf-8")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise SchemaError(f"source HTTP {exc.code}: {url}") from exc
    except (URLError, UnicodeError, TimeoutError) as exc:
        raise SchemaError(f"cannot read source: {url}: {exc}") from exc


def _version(raw):
    try:
        version = Version(raw)
        return (
            version
            if not (version.is_prerelease or version.is_devrelease or version.local)
            else None
        )
    except InvalidVersion:
        return None


def _wheel(files, package, version, role):
    # Universal wheels rank first. Authors remain responsible for choosing a
    # supported platform and recording the complete transitive wheel closure.
    eligible = [f for f in files if f.get("packagetype") == "bdist_wheel" and not f.get("yanked")]
    eligible.sort(
        key=lambda f: (
            not f.get("filename", "").endswith("py3-none-any.whl"),
            f.get("filename", ""),
        )
    )
    for f in eligible:
        try:
            _url(f.get("url"), "wheel URL")
            _hash(f.get("digests", {}).get("sha256"), "wheel sha256")
            if not urlsplit(f["url"]).path.endswith(".whl"):
                continue
            return dict(
                name=package,
                version=version,
                role=role,
                url=f["url"],
                sha256=f["digests"]["sha256"],
            )
        except SchemaError:
            continue
    return None


def _excerpt(body):
    # A contiguous verbatim slice, not a summary. Center long notes on the first
    # marker so the oracle cannot lose the reason this candidate was ranked.
    matches = [m for _, pattern in MARKERS.values() if (m := re.search(pattern, body, re.I))]
    if not matches:
        return None
    start = min(m.start() for m in matches)
    words = list(re.finditer(r"\S+", body))
    index = next((i for i, w in enumerate(words) if w.end() > start), 0)
    first = max(0, index - 60)
    last = min(len(words), first + 400)
    return body[words[first].start() : words[last - 1].end()]


def _notes(info, version, read):
    urls = info.get("project_urls") or {}
    repo = None
    changelogs = []
    for label, url in urls.items():
        if not isinstance(url, str):
            continue
        parsed = urlsplit(url)
        if parsed.hostname == "github.com" and len(parsed.path.strip("/").split("/")) >= 2:
            owner, name = parsed.path.strip("/").split("/")[:2]
            if not repo:
                repo = (owner, name.removesuffix(".git"))
        if re.search(r"changelog|changes|release.?notes", label, re.I):
            if parsed.hostname == "github.com" and "/blob/" in parsed.path:
                url = "https://raw.githubusercontent.com" + parsed.path.replace("/blob/", "/", 1)
            changelogs.append(url)
    if repo:
        owner, name = repo
        for tag in ("v" + version, version):
            endpoint = f"https://api.github.com/repos/{quote(owner)}/{quote(name)}/releases/tags/{quote(tag, safe='')}"
            raw = read(endpoint)
            if raw is not None:
                release = json.loads(raw)
                if (
                    not release.get("draft")
                    and not release.get("prerelease")
                    and release.get("body")
                ):
                    yield release["html_url"], release["body"]
                    break
        # Common canonical changelog paths; declared links are attempted first.
        changelogs.extend(
            f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{path}"
            for path in ("CHANGELOG.md", "CHANGES.md", "HISTORY.md")
        )
    for url in dict.fromkeys(changelogs):
        body = read(url)
        if body is None:
            continue
        # Only the section for this release can supply its oracle; never attach
        # an unrelated historical breaking change to a recent release.
        headings = list(re.finditer(r"^#{1,6}\s+.*$", body, re.M))
        for i, heading in enumerate(headings):
            if re.search(r"(?<![\w.])v?" + re.escape(version) + r"(?![\w.])", heading.group()):
                level = len(heading.group()) - len(heading.group().lstrip("#"))
                end = next(
                    (
                        h.start()
                        for h in headings[i + 1 :]
                        if len(h.group()) - len(h.group().lstrip("#")) <= level
                    ),
                    len(body),
                )
                yield url, body[heading.start() : end]
                break


def mine(packages=DEFAULT_PACKAGES, *, since: str, read=read_url, retrieved_at: str | None = None):
    cutoff = date.fromisoformat(since)
    retrieved_at = retrieved_at or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(retrieved_at)
    candidates = []
    skipped = []
    for package in dict.fromkeys(packages):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package):
            raise SchemaError("invalid PyPI package name")
        package = re.sub(r"[-_.]+", "-", package).lower()
        registry_url = f"https://pypi.org/pypi/{quote(package)}/json"
        raw = read(registry_url)
        if raw is None:
            skipped.append(dict(package=package, reason="PyPI project not found"))
            continue
        registry = json.loads(raw)
        releases = [
            (v, name, files)
            for name, files in registry["releases"].items()
            if (v := _version(name)) is not None
            and files
            and any(not f.get("yanked") for f in files)
        ]
        releases.sort(key=lambda row: row[0])
        for previous, current in zip(releases, releases[1:]):
            old, old_name, old_files = previous
            new, new_name, files = current
            if new.release[:2] == old.release[:2]:
                continue
            dates = [
                datetime.fromisoformat(f["upload_time_iso_8601"].replace("Z", "+00:00")).date()
                for f in files
                if not f.get("yanked")
            ]
            published = min(dates)
            if published <= cutoff:
                continue
            pins = [
                _wheel(old_files, package, old_name, "old"),
                _wheel(files, package, new_name, "new"),
            ]
            if not all(pins):
                skipped.append(
                    dict(package=package, version=new_name, reason="missing old/new wheel pins")
                )
                continue
            best = None
            for url, body in _notes(registry["info"], new_name, read):
                excerpt = _excerpt(body)
                if excerpt is None:
                    continue
                _url(url, "oracle source URL")
                markers = [
                    name
                    for name, (_, pattern) in MARKERS.items()
                    if re.search(pattern, excerpt, re.I)
                ]
                score = sum(MARKERS[name][0] for name in markers)
                candidate = dict(
                    id=f"gap-pypi-{package}-{new_name}",
                    package=package,
                    old_version=old_name,
                    new_version=new_name,
                    release_kind="major" if new.major != old.major else "minor",
                    score=score,
                    markers=markers,
                    packages=pins,
                    cutoff_after=since,
                    provenance=dict(
                        event_date=published.isoformat(), source_urls=[registry_url, url]
                    ),
                    oracle=dict(
                        excerpt=excerpt,
                        source_url=url,
                        retrieved_at=retrieved_at,
                        sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
                    ),
                )
                if best is None or score > best["score"]:
                    best = candidate
            if best:
                candidates.append(best)
            else:
                skipped.append(
                    dict(
                        package=package, version=new_name, reason="no release-specific marked notes"
                    )
                )
    candidates.sort(key=lambda c: (-c["score"], c["package"], Version(c["new_version"])))
    return dict(
        schema_version=1,
        ecosystem="pypi",
        since=since,
        retrieved_at=retrieved_at,
        candidates=candidates,
        skipped=skipped,
    )
