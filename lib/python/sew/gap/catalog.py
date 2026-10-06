"""Offline GAP job catalog gate; listing never opens verifier-only assets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlsplit

from ..catalog import _load_mapping, _reject_unknown, _validate_iso_date, module_root
from ..schema import SchemaError

CODE_FAMILIES = frozenset(
    {
        "api-break",
        "silent-default",
        "release-breakage",
        "vulnerable-dependency",
        "changed-third-party-api",
    }
)
BRIEF_FAMILIES = frozenset({"decision-brief", "research-brief"})
FAMILIES = CODE_FAMILIES | BRIEF_FAMILIES | {"control"}
COMMON = {
    "id",
    "family",
    "kind",
    "task_type",
    "prompt",
    "hidden",
    "packages",
    "oracle",
    "verifier",
    "provenance",
    "cutoff_after",
    "budgets",
    "giveaway_terms",
}
CODE = {"fixture", "third_party_packages"}
BRIEF = {"deliverable_schema", "rubric"}


def _mapping(value: object, required: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise SchemaError(f"{label} must be a mapping")
    _reject_unknown(value, required, label)
    missing = required - value.keys()
    if missing:
        raise SchemaError(f"{label} missing fields: {', '.join(sorted(missing))}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{label} must be a non-empty string")
    return value


def _strings(value: object, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not empty and not value):
        raise SchemaError(f"{label} must be a {'possibly empty' if empty else 'non-empty'} list")
    for entry in value:
        _text(entry, label)
    return value


def _positive(value: object, label: str, *, zero: bool = False) -> None:
    if type(value) is not int or value < (0 if zero else 1):
        raise SchemaError(f"{label} must be a {'nonnegative' if zero else 'positive'} integer")


def _url(value: object, label: str) -> None:
    try:
        parsed = urlsplit(_text(value, label))
    except ValueError as exc:
        raise SchemaError(f"{label} must be a valid HTTPS URL") from exc
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise SchemaError(f"{label} must be an HTTPS URL without credentials")


def _hash(value: object, label: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise SchemaError(f"{label} must be a lowercase sha256")


def _path(base: Path, value: object, label: str, *, directory: bool) -> Path:
    relative = Path(_text(value, label))
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise SchemaError(f"{label} must be a catalog-relative path")
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise SchemaError(f"{label} escapes the catalog")
    if not (resolved.is_dir() if directory else resolved.is_file()):
        raise SchemaError(f"{label} does not exist with the required type")
    return resolved


def validate_task(task: object, catalog_dir: Path) -> dict:
    """Validate one task, preserving its declared relative paths and excerpt bytes.

    Excerpts live inline in tasks.yaml. Hidden directories and rubric references
    are stat-checked only; their contents belong to the verifier, not this loader.
    """
    if not isinstance(task, dict):
        raise SchemaError("GAP task must be a mapping")
    task_type = task.get("task_type")
    if not isinstance(task_type, str) or task_type not in {"code", "brief"}:
        raise SchemaError("task_type must be code or brief")
    task = _mapping(task, COMMON | (CODE if task_type == "code" else BRIEF), "GAP task")
    identifier = _text(task["id"], "id")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier):
        raise SchemaError("id must be a lowercase hyphenated identifier")
    family = task["family"]
    if not isinstance(family, str) or family not in FAMILIES:
        raise SchemaError("unknown GAP family")
    if task["kind"] != ("control" if family == "control" else "gap"):
        raise SchemaError("kind must agree with family")
    if (family in CODE_FAMILIES and task_type != "code") or (
        family in BRIEF_FAMILIES and task_type != "brief"
    ):
        raise SchemaError("task_type must agree with family")
    prompt = _text(task["prompt"], "prompt")
    hidden = _path(catalog_dir, task["hidden"], "hidden", directory=True)
    if task_type == "code":
        fixture = _path(catalog_dir, task["fixture"], "fixture", directory=True)
        if hidden.is_relative_to(fixture) or fixture.is_relative_to(hidden):
            raise SchemaError("hidden assets and fixture must be disjoint")
        # A fixture symlink can expose hidden assets even when the two roots
        # are siblings. Inspect fixture paths only, never traverse hidden data.
        for entry in fixture.rglob("*"):
            if entry.is_symlink():
                target = entry.resolve()
                if not target.is_relative_to(fixture):
                    raise SchemaError("fixture symlink escapes fixture (may expose hidden assets)")
    else:
        if not isinstance(task["deliverable_schema"], dict) or not task["deliverable_schema"]:
            raise SchemaError("deliverable_schema must be a non-empty mapping")
        _path(catalog_dir, task["rubric"], "rubric", directory=False)
    oracle = _mapping(task["oracle"], {"excerpt", "source_url", "retrieved_at", "sha256"}, "oracle")
    excerpt = _text(oracle["excerpt"], "oracle.excerpt")
    if len(excerpt.split()) > 400:
        raise SchemaError("oracle excerpt exceeds 400 words")
    _url(oracle["source_url"], "oracle.source_url")
    _validate_iso_date(oracle["retrieved_at"], "oracle.retrieved_at")
    _hash(oracle["sha256"], "oracle.sha256")
    if hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != oracle["sha256"]:
        raise SchemaError("oracle hash mismatch")
    terms = _strings(task["giveaway_terms"], "giveaway_terms", empty=task["kind"] == "control")
    for term in terms:
        pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
        if not re.search(pattern, excerpt, re.IGNORECASE):
            raise SchemaError("giveaway term is absent from oracle")
        if re.search(pattern, prompt, re.IGNORECASE):
            raise SchemaError("prompt contains oracle giveaway term")
    verifier = _mapping(
        task["verifier"], {"visible_commands", "hidden_commands", "timeout_seconds"}, "verifier"
    )
    for key in ("visible_commands", "hidden_commands"):
        _strings(verifier[key], f"verifier.{key}")
    _positive(verifier["timeout_seconds"], "verifier.timeout_seconds")
    provenance = _mapping(task["provenance"], {"event_date", "source_urls"}, "provenance")
    _validate_iso_date(provenance["event_date"], "provenance.event_date")
    for url in _strings(provenance["source_urls"], "provenance.source_urls"):
        _url(url, "provenance.source_urls")
    _validate_iso_date(task["cutoff_after"], "cutoff_after")
    if task["kind"] == "gap" and provenance["event_date"] <= task["cutoff_after"]:
        raise SchemaError("gap event must postdate cutoff_after")
    budgets = _mapping(
        task["budgets"],
        {"max_total_tokens", "max_wall_clock_seconds", "max_provider_calls"},
        "budgets",
    )
    for key, value in budgets.items():
        _positive(value, f"budgets.{key}", zero=key == "max_provider_calls")
    packages = task["packages"]
    if not isinstance(packages, list):
        raise SchemaError("packages must be a list")
    pins: dict[str, dict[str, str]] = {}
    for package in packages:
        package = _mapping(package, {"name", "version", "url", "sha256", "role"}, "package")
        name = re.sub(r"[-_.]+", "-", _text(package["name"], "package.name")).lower()
        version = _text(package["version"], "package.version")
        _url(package["url"], "package.url")
        if not urlsplit(package["url"]).path.endswith(".whl"):
            raise SchemaError("package URL must name a wheel")
        _hash(package["sha256"], "package.sha256")
        role = package["role"]
        if not isinstance(role, str) or role not in {"old", "new", "dependency"}:
            raise SchemaError("package role must be old, new or dependency")
        if role in pins.setdefault(name, {}):
            raise SchemaError("duplicate package role")
        pins[name][role] = version
    if task_type == "code":
        dependencies = _strings(task["third_party_packages"], "third_party_packages", empty=True)
        declared = {re.sub(r"[-_.]+", "-", name).lower() for name in dependencies}
        if declared != set(pins):
            raise SchemaError("third-party packages must exactly match pinned packages")
        if task["kind"] == "gap" and not any(
            "old" in roles and "new" in roles and roles["old"] != roles["new"]
            for roles in pins.values()
        ):
            raise SchemaError("gap code requires distinct old and new wheels for the same package")
    return task


def load_gap_tasks(root: Path | None = None) -> dict[str, dict]:
    """Load validated tasks keyed by id; root is the SEW module root."""
    base = (module_root() if root is None else Path(root)) / "catalogs" / "gap"
    catalog = _mapping(
        _load_mapping(base / "tasks.yaml", "GAP catalog"),
        {"schema_version", "catalog_id", "authoring_policy", "tasks"},
        "GAP catalog",
    )
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        raise SchemaError("unsupported GAP schema_version")
    _text(catalog["catalog_id"], "catalog_id")
    _text(catalog["authoring_policy"], "authoring_policy")
    if not isinstance(catalog["tasks"], list):
        raise SchemaError("catalog.tasks must be a list")
    records = {}
    for raw in catalog["tasks"]:
        task = validate_task(raw, base)
        if task["id"] in records:
            raise SchemaError(f"duplicate task id: {task['id']}")
        records[task["id"]] = task
    return records


def validate_gap_catalog(root: Path | None = None) -> list[str]:
    """List task ids after metadata validation, without reading hidden assets."""
    return list(load_gap_tasks(root))
