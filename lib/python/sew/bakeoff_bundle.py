"""WSB-10 reproducibility bundle, explain view, and published headline report.

A benchmark that cannot be checked is an assertion. The bundle packages one
suite run so that an outside reader -- including one at a vendor whose product
lost -- can re-derive every aggregate in the published report, or find the flaw.

Four rules carry the module.

**The bundle re-derives the report, or it is not written.** A bundle holds a
self-contained suite run root (runner state, run index, and every run's
records) beside the task manifests, arm declarations, and price table the
report read. Its report is generated from the bundle itself, and before
anything is published the bundler compares those aggregates with a report over
the source run: any difference refuses the bundle. ``verify_bundle`` repeats
the derivation for a reader, and checks every file against the inventory.

**Redaction is the default, and every stripped field is listed.** Transcript
text outside protocol fields (the prompt, model output, tool inputs and tool
results) and harness stderr are withheld unless the operator opts in for the
run. Protocol fields -- event types, tool names, call ids, models, usage
counters -- stay, because contamination, the billed model, and token usage are
re-derived from them. The redaction report lists, by file and JSON pointer,
each field the bundler stripped and each placeholder the capture already left.

**No credential material, even under opt-in.** Every record passes the
credential scrub: values under credential-named keys, credential-shaped text,
and the literal values of credential env vars on the bundling host. The
finished bundle is then swept, and a finding refuses it rather than publishing
it. Assembly happens in a staging directory that is moved into place only
after the sweep, so a refused bundle never exists at its destination.

**Packaging, not analysis.** Every figure in the published report and in the
explain view is read from the WSB-09 report and the run's own records.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import statistics
import tarfile
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .bakeoff_report import (
    ALL_TASK_CLASSES,
    ATTEMPTED,
    DEFAULT_CONTROLS,
    FAILURE,
    JSON_REPORT_NAME,
    MARKDOWN_REPORT_NAME,
    REPORT_SCHEMA_VERSION,
    SPAWN_METADATA_REF,
    SUCCESS,
    TRANSCRIPT_REF,
    Control,
    _arm_label,
    _link,
    _marks_text,
    _per_success_text,
    _rate_text,
    _ratio_text,
    _success_delta_text,
    _tokens_per_success_text,
    _tokens_text,
    build_bakeoff_report,
    render_bakeoff_markdown,
)
from .catalog import module_root
from .cost_model import PriceTableError, default_price_table_path, load_price_table
from .harness import AUTHORIZATION_HEADER_VALUE_RE, BEARER_TOKEN_SHAPE_RE, COOKIE_HEADER_VALUE_RE
from .live_harness import PROTOCOL_KEYS, TRANSCRIPT_SECRET_KEY_RE
from .mcp_meter import AVAILABILITY_REF
from .live_harness import scrub_text as scrub_credentials
from .report import ReportError, _ci, _load_index, _load_state, _md_cell, _table
from .schema import SchemaError, validate_artifact_path
from .state import default_state_root

BUNDLE_SCHEMA_VERSION = 1
MANIFEST_NAME = "bundle-manifest.json"
REDACTION_REPORT_NAME = "redaction-report.json"
PUBLISHED_REPORT_NAME = "README.md"
RUNS_DIR = "runs"
MANIFESTS_DIR = "manifests"
ARMS_NAME = "arms.json"
PRICE_TABLE_NAME = "price-table.yaml"
PUBLICATION_DIR = "publication"
STDERR_REF = "artifacts/harness-stderr.txt"
RUN_STATE_FILES = ("runner-state.json", "run-index.json")

RECORD_SUFFIXES = frozenset({".json", ".yaml", ".yml"})
TEXT_SUFFIXES = frozenset({".md", ".txt"})
# Transcript keys kept under redaction: the protocol fields the live driver
# never elides, plus the tool surface a harness announces at init.
TRANSCRIPT_KEPT_KEYS = PROTOCOL_KEYS | {"tools"}
# A protocol key is meaningful only in an event, harness event, message, or
# typed message block. Unknown nested objects are transcript payloads.
# Only typed blocks in a message's content list carry these protocol fields.
# In particular, a tool_result's own content is raw even when it is structured.
TRANSCRIPT_BLOCK_KEYS = {
    "text": frozenset({"type"}),
    "tool_use": frozenset({"type", "id", "name"}),
    "tool_result": frozenset({"type", "tool_use_id"}),
}
# Codex native pricing reads only these fields from completed web_search items.
# The action's query, URL, and other payload fields remain transcript text.
CODEX_ITEM_KEYS = frozenset({"type", "id"})
CODEX_ACTION_KEYS = frozenset({"type"})
TRANSCRIPT_USAGE_COUNTER_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cached_input_tokens",
        "reasoning_output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "total_billable_tokens",
    }
)
# scrub_text is not idempotent on every shape (a bare bearer token becomes a
# placeholder that its next pass rewrites), so the bundler scrubs to a fixed
# point and the sweep can require one.
MAX_SCRUB_PASSES = 4
# The evaluation dimensions in the order the explain view prints them.
DIMENSIONS = ("completed", "correct", "grounded", "fresh", "schema_valid", "safe")

TRANSCRIPT_PLACEHOLDER = "<redacted:raw-transcript>"
CREDENTIAL_PLACEHOLDER = "<redacted:credential>"
KNOWN_CREDENTIAL_PLACEHOLDER = "<redacted:known-credential>"
# A placeholder the capture or the bundler left in place of stripped content.
PLACEHOLDER_RE = re.compile(r"<(?:redacted|elided)\b[^>]*>")
# Only credential-named env vars can contribute known values. Name boundaries
# avoid treating GIT_AUTHOR_* or OAUTH_BROKER_URL as credential holders.
CREDENTIAL_ENV_NAME_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|token|secret|passw(or)?d|credential|auth)(?:[_-]|$)",
    re.IGNORECASE,
)
# These variables describe how credentials are obtained, rather than holding
# credential bytes. Do not exclude values merely because they are short or
# look like paths: either can be an actual password.
NON_SECRET_CREDENTIAL_ENV_NAMES = frozenset({"SSH_AUTH_SOCK"})
# Credential sources and transport switches hold metadata, not secret bytes.
NON_SECRET_CREDENTIAL_ENV_NAME_RE = re.compile(
    r"(?:_AUTH_PATH|_SECRET_FILE|_TOKEN_VAR|_AUTH_VIA_BROKER|_TOKEN_FROM_AMBIENT_[A-Z0-9_]+)$",
    re.IGNORECASE,
)
CREDENTIAL_SHAPES = (
    ("authorization_header", AUTHORIZATION_HEADER_VALUE_RE),
    ("bearer_token", BEARER_TOKEN_SHAPE_RE),
    ("cookie_header", COOKIE_HEADER_VALUE_RE),
)

# Every reason a field can be stripped, printed with the redaction report.
RAW_TRANSCRIPT = "raw_transcript"
CREDENTIAL_KEY = "credential_key"
CREDENTIAL_SHAPE = "credential_shape"
KNOWN_CREDENTIAL = "known_credential"
HOST_PATH = "host_path"
UNREADABLE_FILE = "unreadable_file"
CAPTURE = "capture"
CREDENTIAL_RULES = frozenset({CREDENTIAL_KEY, CREDENTIAL_SHAPE, KNOWN_CREDENTIAL})
REDACTION_RULES = (
    (
        RAW_TRANSCRIPT,
        "transcript text outside protocol fields (prompt, model output, tool inputs and "
        "results) and harness stderr, withheld; the operator opts in per run",
    ),
    (CREDENTIAL_KEY, "value under a credential-named key (api_key, authorization, cookie, ...)"),
    (CREDENTIAL_SHAPE, "credential-shaped text (auth or cookie header, bearer or API token)"),
    (KNOWN_CREDENTIAL, "the literal value of a credential env var on the bundling host"),
    (HOST_PATH, "absolute host path to a run directory, rewritten relative to the run root"),
    (UNREADABLE_FILE, "file the bundler cannot scrub (binary, symlink, other type); not copied"),
    (CAPTURE, "stripped or elided when the run was captured, before bundling"),
)


class BundleError(ReportError):
    """Raised when a reproducibility bundle cannot be assembled or read."""


@dataclass(frozen=True)
class BundleArtifacts:
    bundle_dir: Path
    archive_path: Path | None
    published_report: Path
    redaction_report: Path
    report_json: Path
    report_markdown: Path
    redacted_fields: int
    raw_transcripts_included: bool


@dataclass(frozen=True)
class ResolvedRun:
    """A suite run root, and where its manifests and price table live."""

    run_root: Path
    module_base: Path | None = None
    price_table_path: Path | None = None
    bundle_dir: Path | None = None


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def assemble_bundle(
    run_root: Path,
    *,
    output_dir: Path | None = None,
    include_raw_transcripts: bool = False,
    module_base: Path | None = None,
    price_table_path: Path | None = None,
    generated_at: str | None = None,
    controls: Sequence[Control] = DEFAULT_CONTROLS,
    environ: Mapping[str, str] | None = None,
    archive: bool = True,
) -> BundleArtifacts:
    """Write ``<output_dir>/<suite_run_id>/`` (and its ``.tar.gz``) for a suite run.

    ``output_dir`` defaults to ``<run_root>/publication``. An existing bundle is
    never overwritten: a publication is immutable once written.
    """

    # The suite run id is the run root's name, which a relative "." does not have.
    run_root = Path(run_root).absolute()
    base = module_base or module_root()
    table_path = price_table_path or default_price_table_path()
    stamp = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    state = _load_state(run_root)
    index = _load_index(run_root, state)
    _validate_run_dirs(run_root, state, index)
    table = _price_table(table_path)
    source_report = build_bakeoff_report(
        run_root, module_base=base, generated_at=stamp, controls=controls, price_table=table
    )
    suite_run_id = str(source_report["suite_run_id"])
    destination = output_dir or run_root / PUBLICATION_DIR
    bundle_dir = destination / suite_run_id
    archive_path = destination / f"{suite_run_id}.tar.gz" if archive else None
    if bundle_dir.exists() and archive_path is not None and not archive_path.exists():
        verified = verify_bundle(bundle_dir, environ=environ)
        legacy_clean = (
            verified.get("report_version_status")
            in {"older_report_version", "legacy_report_version_unverified"}
            and not any(verified["files"][key] for key in ("modified", "missing", "unlisted"))
            and not verified["credential_findings"]
        )
        if not verified["ok"] and not legacy_clean:
            raise BundleError(f"refusing to archive an unverified published bundle: {bundle_dir}")
        manifest = _read_manifest(bundle_dir)
        if (
            manifest.get("suite_run_id") != suite_run_id
            or manifest.get("raw_transcripts_included") != include_raw_transcripts
        ):
            raise BundleError(f"refusing to archive a different published bundle: {bundle_dir}")
        write_archive(bundle_dir, archive_path)
        return _bundle_artifacts(bundle_dir, archive_path, manifest["layout"])
    for path in (bundle_dir, archive_path):
        if path is not None and path.exists():
            raise BundleError(f"refusing to overwrite a published bundle: {path}")

    redactor = _Redactor(
        raw_transcripts=include_raw_transcripts, known_credentials=known_credentials(environ)
    )
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{suite_run_id}.staging-", dir=destination))
    try:
        bundle_run_root = staging / RUNS_DIR / suite_run_id
        _copy_run(run_root, bundle_run_root, state, index, redactor, staging)
        manifests = staging / MANIFESTS_DIR
        _copy_manifests(base, manifests, state, index, redactor, staging)
        redactor.copy_file(table_path, manifests / PRICE_TABLE_NAME, staging)
        bundle_index = _load_index(bundle_run_root, _load_state(bundle_run_root))
        _write_json(manifests / ARMS_NAME, _arm_declarations(bundle_index))

        bundle_report = build_bakeoff_report(
            bundle_run_root,
            module_base=manifests,
            generated_at=stamp,
            controls=controls,
            price_table=_price_table(manifests / PRICE_TABLE_NAME),
        )
        source_view = aggregate_view(source_report, _path_tokens(run_root, index))
        bundle_view = aggregate_view(bundle_report, _path_tokens(bundle_run_root, bundle_index))
        drift = differences(source_view, bundle_view)
        if drift:
            raise BundleError(
                "bundle aggregates drifted from the source run's report at "
                f"{', '.join(drift[:5])}; not published"
            )
        published = _replace_paths(bundle_report, _path_tokens(bundle_run_root, bundle_index))
        reports = bundle_run_root / "reports"
        _write_json(reports / JSON_REPORT_NAME, published)
        (reports / MARKDOWN_REPORT_NAME).write_text(
            render_bakeoff_markdown(published), encoding="utf-8"
        )

        digest = _digest(bundle_view)
        run_prefix = f"{RUNS_DIR}/{suite_run_id}"
        layout = {
            "run_root": run_prefix,
            "manifests": MANIFESTS_DIR,
            "price_table": f"{MANIFESTS_DIR}/{PRICE_TABLE_NAME}",
            "arm_declarations": f"{MANIFESTS_DIR}/{ARMS_NAME}",
            "report_json": f"{run_prefix}/reports/{JSON_REPORT_NAME}",
            "report_markdown": f"{run_prefix}/reports/{MARKDOWN_REPORT_NAME}",
            "redaction_report": REDACTION_REPORT_NAME,
            "published_report": PUBLISHED_REPORT_NAME,
        }
        redaction = redactor.report(suite_run_id)
        _write_json(staging / REDACTION_REPORT_NAME, redaction)
        (staging / PUBLISHED_REPORT_NAME).write_text(
            render_published_report(
                published,
                aggregates_sha256=digest,
                raw_transcripts_included=include_raw_transcripts,
                redaction_summary=redaction["summary"],
                layout=layout,
            ),
            encoding="utf-8",
        )
        _write_json(
            staging / MANIFEST_NAME,
            _manifest(published, staging, layout, digest, include_raw_transcripts),
        )
        findings = sweep_bundle(staging, redactor.known_credentials)
        if findings:
            raise BundleError(
                "credential sweep refused the bundle: "
                + "; ".join(findings[:5])
                + "; not published"
            )
        # mkdtemp stages privately; the publication itself is meant to be read.
        staging.chmod(0o755)
        staging.rename(bundle_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if archive_path is not None:
        write_archive(bundle_dir, archive_path)
    return _bundle_artifacts(bundle_dir, archive_path, layout)


def _bundle_artifacts(
    bundle_dir: Path, archive_path: Path | None, layout: Mapping[str, str]
) -> BundleArtifacts:
    redaction = _read_json(bundle_dir / REDACTION_REPORT_NAME)
    manifest = _read_manifest(bundle_dir)
    return BundleArtifacts(
        bundle_dir=bundle_dir,
        archive_path=archive_path,
        published_report=bundle_dir / PUBLISHED_REPORT_NAME,
        redaction_report=bundle_dir / REDACTION_REPORT_NAME,
        report_json=bundle_dir / layout["report_json"],
        report_markdown=bundle_dir / layout["report_markdown"],
        redacted_fields=redaction["summary"]["fields"],
        raw_transcripts_included=manifest["raw_transcripts_included"],
    )


def _safe_run_dir(run_root: Path, value: str) -> Path:
    source = Path(value) if Path(value).is_absolute() else run_root / value
    resolved = source.resolve()
    # Resolve bundles/ itself: a suite may keep it on another disk behind a
    # symlink. Symlinks below bundles/ are still refused.
    bundles = (run_root / "bundles").resolve()
    if not resolved.is_relative_to(bundles) or resolved == bundles:
        raise BundleError(f"indexed run directory escapes suite bundles/: {value}")
    stops = {run_root, run_root / "bundles", bundles}
    for component in (source, *source.parents):
        if component in stops:
            break
        if component.is_symlink():
            raise BundleError(f"indexed run directory uses a symlink in suite bundles/: {value}")
    return resolved


def _validate_run_dirs(
    run_root: Path, state: Mapping[str, Any], index: Sequence[Mapping[str, Any]]
) -> None:
    """Refuse external run paths before the source report reads any evidence."""

    for entries in (state.get("index"), index):
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, Mapping):
                continue
            for value in (entry.get("run_dir"), *(entry.get("attempt_run_dirs") or [])):
                if isinstance(value, str) and value:
                    _safe_run_dir(run_root, value)
    deferred = state.get("deferred_unavailable")
    for record in deferred.values() if isinstance(deferred, Mapping) else []:
        attempts = record.get("attempt_run_dirs") if isinstance(record, Mapping) else None
        for value in attempts if isinstance(attempts, list) else []:
            if isinstance(value, str) and value:
                _safe_run_dir(run_root, value)


def _copy_run(
    run_root: Path,
    dest: Path,
    state: Mapping[str, Any],
    index: Sequence[Mapping[str, Any]],
    redactor: _Redactor,
    bundle_root: Path,
) -> None:
    """Copy every indexed run directory under ``bundles/`` and point the index at it."""

    placed: dict[Path, str] = {}
    suite_bundles = run_root.resolve() / "bundles"

    def place(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        source = _safe_run_dir(run_root, value)
        if source not in placed:
            name = source.name
            if not name or name in {".", ".."}:
                raise BundleError(f"run directory has no usable name: {source}")
            used = set(placed.values())
            if name in used:
                # Two runs share a basename (`bundles/A/run1`, `bundles/B/run1`):
                # name the later one by its path under the suite's bundles/.
                name = source.relative_to(suite_bundles).as_posix().replace("/", "__")
            candidate, suffix = name, 2
            while candidate in used:
                candidate, suffix = f"{name}-{suffix}", suffix + 1
            placed[source] = candidate
        return f"bundles/{placed[source]}"

    def rewrite_attempts(values: list[Any], file_name: str, where: str) -> list[Any]:
        attempts = []
        for number, value in enumerate(values):
            relative = place(value)
            if relative is not None and value != relative:
                redactor.note(file_name, f"{where}/attempt_run_dirs/{number}", HOST_PATH)
            attempts.append(relative if relative is not None else value)
        return attempts

    def rewrite(entries: Any, file_name: str, pointer: str) -> list[dict[str, Any]]:
        rewritten = []
        for position, raw in enumerate(entries if isinstance(entries, list) else []):
            if not isinstance(raw, dict):
                continue
            entry = dict(raw)
            where = f"{pointer}/{position}"
            relative = place(entry.get("run_dir"))
            if relative is not None:
                if entry["run_dir"] != relative:
                    redactor.note(file_name, f"{where}/run_dir", HOST_PATH)
                entry["run_dir"] = relative
            if isinstance(entry.get("attempt_run_dirs"), list):
                entry["attempt_run_dirs"] = rewrite_attempts(
                    entry["attempt_run_dirs"], file_name, where
                )
            rewritten.append(entry)
        return rewritten

    prefix = _relative(dest, bundle_root)
    index_path = run_root / "run-index.json"
    raw_index = _read_json(index_path) if index_path.is_file() else list(index)
    bundle_index = rewrite(raw_index, f"{prefix}/run-index.json", "")
    bundle_state = dict(state)
    bundle_state["index"] = rewrite(state.get("index"), f"{prefix}/runner-state.json", "/index")
    deferred = state.get("deferred_unavailable")
    if isinstance(deferred, Mapping):
        # Cells a provider_unavailable streak stop took out of the index keep
        # their walled bundles here; ship those bundles with relative paths too.
        bundle_deferred = {}
        for cell_key, raw in deferred.items():
            record = dict(raw) if isinstance(raw, Mapping) else raw
            if isinstance(record, dict) and isinstance(record.get("attempt_run_dirs"), list):
                record["attempt_run_dirs"] = rewrite_attempts(
                    record["attempt_run_dirs"],
                    f"{prefix}/runner-state.json",
                    f"/deferred_unavailable/{_escape(str(cell_key))}",
                )
            bundle_deferred[cell_key] = record
        bundle_state["deferred_unavailable"] = bundle_deferred
    for name, value in (("runner-state.json", bundle_state), ("run-index.json", bundle_index)):
        redactor.write_record(value, dest / name, bundle_root)
    for source, name in sorted(placed.items(), key=lambda item: item[1]):
        if source.is_dir():
            redactor.copy_tree(source, dest / "bundles" / name, bundle_root)


def _copy_manifests(
    module_base: Path,
    dest: Path,
    state: Mapping[str, Any],
    index: Sequence[Mapping[str, Any]],
    redactor: _Redactor,
    bundle_root: Path,
) -> None:
    """Snapshot the task manifests and catalogs the report reads task classes from."""

    task_ids = sorted(
        {str(entry["task_id"]) for entry in index if _component(entry.get("task_id")) is not None}
    )
    needs_production = False
    for task_id in task_ids:
        source = module_base / "tasks" / task_id
        if (source / "task.yaml").is_file():
            redactor.copy_tree(source, dest / "tasks" / task_id, bundle_root)
        else:
            needs_production = True
    catalogs = {suite} if (suite := _component(state.get("suite_id"))) else set()
    if needs_production:
        catalogs.add("production")
    for catalog in sorted(catalogs):
        source = module_base / "catalogs" / catalog
        if source.is_dir():
            redactor.copy_tree(source, dest / "catalogs" / catalog, bundle_root)


def _arm_declarations(index: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Each arm's declared tool surface, as recorded at spawn for its runs."""

    arms: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in index:
        key = tuple(str(entry.get(name) or "") for name in _ARM_FIELDS)
        arm = arms.setdefault(
            key,
            {
                "arm": _arm_label(key),
                **dict(zip(_ARM_FIELDS, key, strict=True)),
                "cells": 0,
                "run_ids": [],
                "declared_contracts": [],
                "provider_exposures": [],
            },
        )
        arm["cells"] += 1
        if isinstance(entry.get("run_id"), str):
            arm["run_ids"].append(entry["run_id"])
        run_dir = entry.get("run_dir")
        spawn = _read_optional(Path(run_dir) / SPAWN_METADATA_REF) if run_dir else None
        for source, target in (
            ("arm_contract", "declared_contracts"),
            ("provider_exposure", "provider_exposures"),
        ):
            value = (spawn or {}).get(source)
            if isinstance(value, dict) and value not in arm[target]:
                arm[target].append(value)
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "description": (
            "declared tool surface per arm, from each run's spawn metadata; the WSB-06 "
            "transcript audit of every run is in its artifacts/spawn-metadata.json"
        ),
        "arms": [
            {**arm, "run_ids": sorted(arm["run_ids"])}
            for _, arm in sorted(arms.items(), key=lambda item: item[0])
        ],
    }


_ARM_FIELDS = ("harness_id", "provider_id", "model_profile")


def _manifest(
    report: Mapping[str, Any],
    root: Path,
    layout: Mapping[str, str],
    digest: str,
    raw_transcripts_included: bool,
) -> dict[str, Any]:
    files = [
        {"path": rel, "sha256": _sha256(path), "size_bytes": path.stat().st_size}
        for rel, path in _files(root)
        if rel != MANIFEST_NAME
    ]
    paths = [item["path"] for item in files]
    run_prefix = f"{layout['run_root']}/bundles/"

    def run_files(pattern: str) -> list[str]:
        return [
            path
            for path in paths
            if path.startswith(run_prefix) and PurePosixPath(path).match(pattern)
        ]

    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "bundle": "wsb-reproducibility-bundle",
        "report_schema_version": report.get("schema_version"),
        "suite_id": report.get("suite_id"),
        "suite_version": report.get("suite_version"),
        "suite_run_id": report.get("suite_run_id"),
        "mode": report.get("mode"),
        "seed": report.get("seed"),
        "generated_at": report.get("generated_at"),
        "raw_transcripts_included": raw_transcripts_included,
        "credentials_included": False,
        "aggregates_sha256": digest,
        "layout": dict(layout),
        "contents": {
            "task_manifests": [p for p in paths if p.startswith(f"{MANIFESTS_DIR}/tasks/")]
            + [p for p in paths if p.startswith(f"{MANIFESTS_DIR}/catalogs/")],
            "arm_declarations": [layout["arm_declarations"]],
            "transcripts": run_files(TRANSCRIPT_REF),
            "usage_records": run_files("metrics/metrics.json"),
            "provider_calls": [
                path
                for path in run_files("provider-calls/*.json")
                if not path.endswith(f"/{AVAILABILITY_REF}")
            ],
            "provider_availability": run_files(AVAILABILITY_REF),
            "judge_records": run_files("evaluations/*.json"),
            "metrics": [layout["report_json"], layout["report_markdown"]],
            "redaction_report": [layout["redaction_report"]],
        },
        "verify": "sew bakeoff verify <bundle-dir>",
        "files": files,
    }


def write_archive(bundle_dir: Path, archive_path: Path) -> Path:
    """A reproducible ``.tar.gz`` of the bundle: sorted, zero mtimes, no owners."""

    # Stage under a unique name: two bundle processes archiving the same run
    # must never share (and interleave writes into) one staging file.
    fd, staging = tempfile.mkstemp(
        prefix=f".{archive_path.name}.", suffix=".partial", dir=archive_path.parent
    )
    partial = Path(staging)
    entries = [bundle_dir, *sorted(bundle_dir.rglob("*"))]
    try:
        with (
            os.fdopen(fd, "wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed,
            tarfile.open(fileobj=compressed, mode="w") as tar,
        ):
            for path in entries:
                info = tar.gettarinfo(
                    str(path), arcname=path.relative_to(bundle_dir.parent).as_posix()
                )
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                info.mode = 0o755 if path.is_dir() else 0o644
                if path.is_file():
                    with path.open("rb") as handle:
                        tar.addfile(info, handle)
                else:
                    tar.addfile(info)
        partial.replace(archive_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return archive_path


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


@dataclass
class _Redactor:
    """Copies files into a bundle, stripping what may not be published.

    Every strip is recorded by file and JSON pointer. ``sweep_bundle`` also runs
    a finished bundle back through ``scrub_document``: a publishable bundle is a
    fixed point of the credential rules.
    """

    raw_transcripts: bool
    known_credentials: tuple[str, ...] = ()
    files: dict[str, dict[str, Any]] = field(default_factory=dict)

    def note(self, path: str, pointer: str, rule: str, chars: int | None = None) -> None:
        entry: dict[str, Any] = {"pointer": pointer, "rule": rule}
        if chars is not None:
            entry["chars"] = chars
        self.files.setdefault(path, {"path": path, "fields": []})["fields"].append(entry)

    def copy_tree(self, source: Path, dest: Path, bundle_root: Path) -> None:
        for path in sorted(source.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            self.copy_file(path, dest / path.relative_to(source), bundle_root)

    def copy_file(self, source: Path, dest: Path, bundle_root: Path) -> None:
        rel = _relative(dest, bundle_root)
        if source.is_symlink() or not source.is_file():
            self.note(rel, "", UNREADABLE_FILE)
            return
        data = source.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        if text is None or (source.suffix not in RECORD_SUFFIXES | TEXT_SUFFIXES):
            self.note(rel, "", UNREADABLE_FILE)
            return
        # Lets a holder of the captured file check that the redacted one derives from it.
        self.files.setdefault(rel, {"path": rel, "fields": []})["source_sha256"] = hashlib.sha256(
            data
        ).hexdigest()
        transcript = not self.raw_transcripts and rel.endswith(f"/{TRANSCRIPT_REF}")
        stderr = not self.raw_transcripts and rel.endswith(f"/{STDERR_REF}")
        output = self.scrub_document(text, source.suffix, rel, transcript=transcript, stderr=stderr)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data if output == text else output.encode("utf-8"))

    def write_record(self, value: Any, dest: Path, bundle_root: Path) -> None:
        rel = _relative(dest, bundle_root)
        _write_json(dest, self.scrub(value, rel, "", transcript=False))

    def scrub_document(
        self, text: str, suffix: str, rel: str, *, transcript: bool = False, stderr: bool = False
    ) -> str:
        """The publishable text of one file; unchanged text when nothing is stripped."""

        if stderr:
            if not text:
                return text
            self.note(rel, "", RAW_TRANSCRIPT, len(text))
            return f"{TRANSCRIPT_PLACEHOLDER}\n"
        value = _parse(text, suffix) if suffix in RECORD_SUFFIXES else _UNPARSED
        if value is _UNPARSED:
            if transcript and text:
                # A transcript that does not parse cannot be split into protocol
                # and content, so all of it is content.
                self.note(rel, "", RAW_TRANSCRIPT, len(text))
                return f"{TRANSCRIPT_PLACEHOLDER}\n"
            return self.scrub_text(text, rel, "")
        scrubbed = self.scrub(value, rel, "", transcript=transcript)
        if scrubbed == value:
            serialized = text
        elif suffix == ".json":
            serialized = json.dumps(scrubbed, indent=2, sort_keys=True) + "\n"
        else:
            serialized = yaml.safe_dump(scrubbed, sort_keys=False, allow_unicode=True)
        # The value walk never sees JSON keys or YAML comments, so a credential
        # there would survive it. Sweep the serialized text with the same rules.
        return self._strip_credentials(serialized, rel, "")

    def scrub(
        self,
        value: Any,
        rel: str,
        pointer: str,
        *,
        transcript: bool,
        position: str = "event",
        block_keys: frozenset[str] | None = None,
    ) -> Any:
        """``value`` with credentials stripped and, for a withheld transcript, its text.

        Only known event positions, Codex web_search items, and typed blocks
        can retain protocol fields.
        """

        if isinstance(value, Mapping):
            result = {}
            for key, item in value.items():
                child = f"{pointer}/{_escape(str(key))}"
                if (
                    isinstance(key, str)
                    and TRANSCRIPT_SECRET_KEY_RE.search(key)
                    and _is_secret_value(item)
                ):
                    self.note(rel, child, CREDENTIAL_KEY)
                    result[key] = CREDENTIAL_PLACEHOLDER
                elif (
                    transcript
                    and (
                        (
                            position in {"event", "harness_event", "message"}
                            and key in TRANSCRIPT_KEPT_KEYS
                            and (key != "tools" or position == "harness_event")
                        )
                        or (position == "block" and key in (block_keys or ()))
                        or (position == "codex_item" and key in CODEX_ITEM_KEYS)
                        or (
                            position == "codex_action"
                            and key in CODEX_ACTION_KEYS
                            and isinstance(item, str)
                            and item in {"search", "open_page", "find_in_page", "other"}
                        )
                    )
                    and _is_protocol_value(item)
                ):
                    result[key] = self.scrub(item, rel, child, transcript=False)
                elif (
                    transcript
                    and position == "usage"
                    and key in TRANSCRIPT_USAGE_COUNTER_KEYS
                    and isinstance(item, (int, float))
                    and not isinstance(item, bool)
                ):
                    result[key] = item
                elif (
                    transcript
                    and position == "usage"
                    and key in {"input_tokens_details", "output_tokens_details"}
                    and isinstance(item, Mapping)
                ):
                    result[key] = self.scrub(item, rel, child, transcript=True, position="usage")
                elif (
                    transcript
                    and position == "message"
                    and key == "content"
                    and isinstance(item, list)
                ):
                    result[key] = [
                        self.scrub(
                            block,
                            rel,
                            f"{child}/{index}",
                            transcript=True,
                            position="block",
                            block_keys=TRANSCRIPT_BLOCK_KEYS.get(block.get("type"))
                            if isinstance(block, Mapping)
                            else None,
                        )
                        for index, block in enumerate(item)
                    ]
                else:
                    child_position = "payload"
                    if transcript and position == "event" and key == "harness_event":
                        child_position = "harness_event"
                    elif (
                        transcript
                        and position == "harness_event"
                        and key == "item"
                        and isinstance(item, Mapping)
                        and item.get("type") == "web_search"
                    ):
                        child_position = "codex_item"
                    elif (
                        transcript
                        and position == "codex_item"
                        and key == "action"
                        and isinstance(item, Mapping)
                    ):
                        child_position = "codex_action"
                    elif transcript and position == "harness_event" and key == "message":
                        child_position = "message"
                    elif (
                        transcript
                        and position in {"event", "harness_event", "message"}
                        and key == "usage"
                    ):
                        child_position = "usage"
                    result[key] = self.scrub(
                        item,
                        rel,
                        child,
                        transcript=transcript,
                        position=child_position,
                    )
            return result
        if isinstance(value, list):
            return [
                self.scrub(
                    item,
                    rel,
                    f"{pointer}/{position}",
                    transcript=transcript,
                    position="event" if not pointer else "payload",
                )
                for position, item in enumerate(value)
            ]
        if isinstance(value, str):
            if transcript and value:
                self.note(rel, pointer, RAW_TRANSCRIPT, len(value))
                return TRANSCRIPT_PLACEHOLDER
            return self.scrub_text(value, rel, pointer)
        if transcript and value is not None:
            self.note(rel, pointer, RAW_TRANSCRIPT)
            return TRANSCRIPT_PLACEHOLDER
        return value

    def scrub_text(self, value: str, rel: str, pointer: str) -> str:
        known = self._strip_credentials(value, rel, pointer)
        if known == value and PLACEHOLDER_RE.search(value):
            self.note(rel, pointer, CAPTURE)
        return known

    def _strip_credentials(self, value: str, rel: str, pointer: str) -> str:
        """``value`` with credential shapes and known credential values replaced."""

        scrubbed = value
        for _ in range(MAX_SCRUB_PASSES):
            again = scrub_credentials(scrubbed)
            if again == scrubbed:
                break
            scrubbed = again
        if scrubbed != value:
            self.note(rel, pointer, CREDENTIAL_SHAPE)
        known = scrubbed
        for secret in self.known_credentials:
            known = known.replace(secret, KNOWN_CREDENTIAL_PLACEHOLDER)
        if known != scrubbed:
            self.note(rel, pointer, KNOWN_CREDENTIAL)
        return known

    def report(self, suite_run_id: str) -> dict[str, Any]:
        files = [entry for _, entry in sorted(self.files.items()) if entry.get("fields")]
        rules = Counter(item["rule"] for entry in files for item in entry["fields"])
        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "suite_run_id": suite_run_id,
            "raw_transcripts_included": self.raw_transcripts,
            "placeholders": [
                TRANSCRIPT_PLACEHOLDER,
                CREDENTIAL_PLACEHOLDER,
                KNOWN_CREDENTIAL_PLACEHOLDER,
                "<redacted:...> (credential shape, from the fleet scrub vocabulary)",
            ],
            "rules": [{"rule": rule, "meaning": meaning} for rule, meaning in REDACTION_RULES],
            # A list, not a mapping: a rule name as a key ("known_credential")
            # reads as a credential-named field to the sweep.
            "summary": {
                "fields": sum(rules.values()),
                "files": len(files),
                "by_rule": [
                    {"rule": rule, "fields": count} for rule, count in sorted(rules.items())
                ],
            },
            "files": files,
        }


_UNPARSED = object()


def _parse(text: str, suffix: str) -> Any:
    try:
        return json.loads(text) if suffix == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError):
        return _UNPARSED


def _is_secret_value(value: Any) -> bool:
    """A value to strip under a credential-named key: anything but a flag or a placeholder."""

    if value is None or isinstance(value, bool) or value in ("", [], {}):
        return False
    return not (isinstance(value, str) and PLACEHOLDER_RE.fullmatch(value))


def _is_protocol_value(value: Any) -> bool:
    return isinstance(value, str) or (
        isinstance(value, list) and all(isinstance(item, str) for item in value)
    )


def _escape(key: str) -> str:
    """One RFC 6901 JSON-pointer reference token."""

    return key.replace("~", "~0").replace("/", "~1")


def known_credentials(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Values of credential env vars on this host, longest first."""

    source = os.environ if environ is None else environ
    values = {
        value
        for name, value in source.items()
        if CREDENTIAL_ENV_NAME_RE.search(name)
        and name not in NON_SECRET_CREDENTIAL_ENV_NAMES
        and not NON_SECRET_CREDENTIAL_ENV_NAME_RE.search(name)
        and isinstance(value, str)
        and bool(value)
    }
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def sweep_bundle(root: Path, known: Sequence[str] = ()) -> list[str]:
    """Credential findings anywhere under ``root``; empty means publishable.

    Three independent checks: the credential-header and token shapes the SEW
    evidence writers refuse, the literal values of known credentials, and a
    re-run of the credential rules, which must find nothing left to strip.
    """

    checker = _Redactor(raw_transcripts=True, known_credentials=tuple(known))
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(f"{rel}: symlink")
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"{rel}: not text, cannot be swept")
            continue
        findings.extend(f"{rel}: {name}" for name, shape in CREDENTIAL_SHAPES if shape.search(text))
        if any(secret in text for secret in known):
            findings.append(f"{rel}: known credential value")
        checker.scrub_document(text, path.suffix, rel)
    for entry in checker.files.values():
        for item in entry["fields"]:
            if item["rule"] in CREDENTIAL_RULES:
                findings.append(f"{entry['path']}{item['pointer'] or ''}: {item['rule']}")
    return sorted(set(findings))


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def verify_bundle(bundle_dir: Path, *, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Re-derive a bundle's aggregates from its own records and check its files."""

    manifest = _read_manifest(bundle_dir)
    layout = manifest["layout"]
    _reject_bundle_symlinks(bundle_dir)
    listed = {item["path"]: item for item in manifest.get("files", []) if isinstance(item, dict)}
    present = {rel: path for rel, path in _files(bundle_dir) if rel != MANIFEST_NAME}
    modified = sorted(
        rel
        for rel, path in present.items()
        if rel in listed and _sha256(path) != listed[rel]["sha256"]
    )
    missing = sorted(set(listed) - set(present))
    unlisted = sorted(set(present) - set(listed))

    run_root = _bundle_member(bundle_dir, layout["run_root"])
    state = _load_state(run_root)
    index = _load_index(run_root, state)
    _validate_run_dirs(run_root, state, index)
    published = _read_json(bundle_dir / layout["report_json"])
    if not isinstance(published, dict):
        raise BundleError(f"{layout['report_json']} must contain the report object")
    report_version = published.get("schema_version")
    manifest_version = manifest.get("report_schema_version")
    version_mismatch = manifest_version is not None and manifest_version != report_version
    if report_version != REPORT_SCHEMA_VERSION or version_mismatch:
        # A new report renderer cannot reproduce an older immutable bundle's
        # aggregate digest or Markdown. Cross-check the manifest's version;
        # legacy manifests cannot authenticate age. Files/credentials are checked.
        findings = sweep_bundle(bundle_dir, known_credentials(environ))
        return {
            "bundle": str(bundle_dir),
            "suite_run_id": manifest.get("suite_run_id"),
            "ok": False,
            "report_version_status": (
                "older_report_version"
                if type(report_version) is int
                and report_version < REPORT_SCHEMA_VERSION
                and not version_mismatch
                and manifest_version == report_version
                else "legacy_report_version_unverified"
                if type(report_version) is int
                and report_version < REPORT_SCHEMA_VERSION
                and manifest_version is None
                else "unsupported_report_version"
            ),
            "report_schema_version": report_version,
            "manifest_report_schema_version": manifest_version,
            "report_version_note": (
                "Aggregates were not re-derived; manifest/report version mismatch."
                if version_mismatch
                else "Aggregates were not re-derived; legacy version is not independently authenticated."
                if manifest_version is None
                else "Aggregates were not re-derived for this report version."
            ),
            "current_report_schema_version": REPORT_SCHEMA_VERSION,
            "files": {
                "checked": len(listed),
                "modified": modified,
                "missing": missing,
                "unlisted": unlisted,
            },
            "aggregates_identical": None,
            "aggregates_sha256": None,
            "manifest_aggregates_sha256": manifest.get("aggregates_sha256"),
            "differences": [],
            "rendered_drift": [],
            "credential_findings": findings,
        }
    derived = build_bakeoff_report(
        run_root,
        module_base=bundle_dir / layout["manifests"],
        generated_at=published.get("generated_at"),
        controls=_controls(published),
        price_table=_price_table(bundle_dir / layout["price_table"]),
    )
    tokens = _path_tokens(run_root, index)
    derived_view = aggregate_view(derived, tokens)
    drift = differences(aggregate_view(published), derived_view)
    verified_report = _replace_paths(derived, tokens)
    redaction = _read_json(bundle_dir / layout["redaction_report"])
    rendered = {
        layout["report_markdown"]: render_bakeoff_markdown(verified_report),
        layout["published_report"]: render_published_report(
            verified_report,
            aggregates_sha256=_digest(derived_view),
            raw_transcripts_included=manifest["raw_transcripts_included"],
            redaction_summary=redaction["summary"],
            layout=layout,
        ),
    }
    rendered_drift = sorted(
        rel
        for rel, expected in rendered.items()
        if (bundle_dir / rel).is_file()
        and (bundle_dir / rel).read_bytes() != expected.encode("utf-8")
    )
    findings = sweep_bundle(bundle_dir, known_credentials(environ))
    digest = _digest(derived_view)
    return {
        "bundle": str(bundle_dir),
        "suite_run_id": manifest.get("suite_run_id"),
        "ok": not (modified or missing or unlisted or drift or rendered_drift or findings)
        and digest == manifest.get("aggregates_sha256"),
        "files": {
            "checked": len(listed),
            "modified": modified,
            "missing": missing,
            "unlisted": unlisted,
        },
        "aggregates_identical": not drift,
        "aggregates_sha256": digest,
        "manifest_aggregates_sha256": manifest.get("aggregates_sha256"),
        "differences": drift[:20],
        "rendered_drift": rendered_drift,
        "credential_findings": findings,
    }


def aggregate_view(report: Mapping[str, Any], tokens: Sequence[tuple[str, str]] = ()) -> Any:
    """What a bundle must reproduce exactly: the report minus its links and timestamp.

    Evidence links are paths relative to wherever the report was written, and
    ``generated_at`` is a clock reading; neither is an aggregate. Absolute run
    paths in diagnostic text are replaced by a ``<run-root>`` token first.
    """

    def strip(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: strip(item) for key, item in value.items() if key != "evidence"}
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    view = strip(_replace_paths(report, tokens))
    view.pop("generated_at", None)
    return view


def differences(left: Any, right: Any, pointer: str = "", limit: int = 50) -> list[str]:
    """JSON pointers at which two values differ, depth first, at most ``limit``."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        found: list[str] = []
        for key in sorted(set(left) | set(right), key=str):
            child = f"{pointer}/{_escape(str(key))}"
            if key not in left or key not in right:
                found.append(child)
            else:
                found.extend(differences(left[key], right[key], child, limit - len(found)))
            if len(found) >= limit:
                break
        return found[:limit]
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        found = []
        for position, (a, b) in enumerate(zip(left, right, strict=True)):
            found.extend(differences(a, b, f"{pointer}/{position}", limit - len(found)))
            if len(found) >= limit:
                break
        return found[:limit]
    return [] if left == right and type(left) is type(right) else [pointer or "/"]


def _path_tokens(run_root: Path, index: Iterable[Mapping[str, Any]]) -> list[tuple[str, str]]:
    """Absolute forms of a run root and its run-directory parent, and their tokens."""

    pairs: dict[str, str] = {}
    for form in (run_root.absolute(), run_root.resolve()):
        pairs[str(form)] = "<run-root>"
    for entry in index:
        run_dir = entry.get("run_dir")
        if isinstance(run_dir, str) and Path(run_dir).is_absolute():
            for form in (Path(run_dir).parent, Path(run_dir).parent.resolve()):
                pairs[str(form)] = "<run-root>/bundles"
    return sorted(
        ((path, token) for path, token in pairs.items() if len(path) > 1),
        key=lambda item: (-len(item[0]), item[0]),
    )


def _replace_paths(value: Any, tokens: Sequence[tuple[str, str]]) -> Any:
    if not tokens:
        return value
    if isinstance(value, Mapping):
        return {key: _replace_paths(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_paths(item, tokens) for item in value]
    if isinstance(value, str):
        for path, token in tokens:
            value = value.replace(path, token)
    return value


def _digest(view: Any) -> str:
    encoded = json.dumps(view, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _controls(report: Mapping[str, Any]) -> tuple[Control, ...]:
    declared = tuple(
        Control(str(item["name"]), str(item["provider_id"]))
        for item in report.get("controls", [])
        if isinstance(item, Mapping) and "name" in item and "provider_id" in item
    )
    return declared or DEFAULT_CONTROLS


# --------------------------------------------------------------------------
# Explain
# --------------------------------------------------------------------------


def resolve_run(target: str, *, state_root: Path | None = None) -> ResolvedRun:
    """A suite run root from a path to a run root or bundle, or a suite run id."""

    path = Path(target).expanduser().absolute()
    if (path / MANIFEST_NAME).is_file():
        layout = _read_manifest(path)["layout"]
        return ResolvedRun(
            run_root=path / layout["run_root"],
            module_base=path / layout["manifests"],
            price_table_path=path / layout["price_table"],
            bundle_dir=path,
        )
    if (path / "runner-state.json").is_file():
        return ResolvedRun(run_root=path)
    if _component(target) is not None:
        candidate = (state_root or default_state_root()) / ".sew" / "runs" / target
        if (candidate / "runner-state.json").is_file():
            return ResolvedRun(run_root=candidate)
    raise ReportError(
        f"{target!r} is not a suite run root, a reproducibility bundle, or a suite run id "
        "under the SEW state root"
    )


def explain_task(
    resolved: ResolvedRun, task_id: str, *, price_table_path: Path | None = None
) -> dict[str, Any]:
    """Per arm, how one task went: successes, failed dimensions, tokens, and notes.

    Dispositions, token provenance, and costs are the WSB-09 report's own; the
    failed dimensions are read from each run's evaluation record.
    """

    table_path = price_table_path or resolved.price_table_path
    if resolved.bundle_dir is not None:
        _reject_bundle_symlinks(resolved.bundle_dir)
    state = _load_state(resolved.run_root)
    index = _load_index(resolved.run_root, state)
    _validate_run_dirs(resolved.run_root, state, index)
    # build_bakeoff_report only reads; output_dir sets evidence links relative
    # to the run root, where _explain_run reads evaluation records.
    report = build_bakeoff_report(
        resolved.run_root,
        module_base=resolved.module_base,
        output_dir=resolved.run_root,
        price_table=_price_table(table_path or default_price_table_path()),
    )
    runs = [run for run in report["runs"] if run["task_id"] == task_id]
    if not runs:
        known = ", ".join(sorted({str(run["task_id"]) for run in report["runs"]})) or "none"
        raise ReportError(
            f"task {task_id!r} has no runs in {report['suite_run_id']}; tasks in this run: {known}"
        )
    details = [_explain_run(run, resolved.run_root) for run in runs]
    arms: dict[str, list[dict[str, Any]]] = {}
    for detail in details:
        arms.setdefault(detail["arm"], []).append(detail)
    order = [row["arm"] for row in report["headline"]]
    return {
        "suite_run_id": report["suite_run_id"],
        "mode": report["mode"],
        "task_id": task_id,
        "task_class": runs[0]["task_class"],
        "run_root": str(resolved.run_root),
        "arms": [
            _explain_arm(arm, arms[arm])
            for arm in sorted(
                arms, key=lambda arm: (order.index(arm) if arm in order else len(order), arm)
            )
        ],
        "runs": sorted(
            details, key=lambda item: (item["arm"], item["repetition"] or 0, item["run_id"])
        ),
    }


def _explain_run(run: Mapping[str, Any], run_root: Path) -> dict[str, Any]:
    evidence = run.get("evidence") or {}
    evaluation = (
        _read_optional(run_root / evidence["evaluation"]) if evidence.get("evaluation") else None
    ) or {}
    dimensions = (
        evaluation.get("dimensions") if isinstance(evaluation.get("dimensions"), dict) else {}
    )
    failed: list[str] = []
    if run["disposition"] == FAILURE:
        if run["status"] == "succeeded":
            ordered = [*DIMENSIONS, *sorted(set(dimensions) - set(DIMENSIONS))]
            failed = [name for name in ordered if dimensions.get(name) is False]
        else:
            # The run ended before delivering; the dimensions a live driver
            # writes for it are placeholders, not verdicts.
            failed = ["completed"]
    judge = evaluation.get("judge") if isinstance(evaluation.get("judge"), dict) else {}
    reasons = evaluation.get("failure_reasons")
    return {
        "run_id": run["run_id"],
        "arm": run["arm"],
        "repetition": run.get("repetition"),
        "status": run["status"],
        "disposition": run["disposition"],
        "failed_dimensions": failed,
        "failure_reasons": [str(item) for item in reasons] if isinstance(reasons, list) else [],
        "judge": judge.get("kind"),
        "tokens": run["tokens"],
        "token_status": run["token_status"],
        "evidence": {
            role: evidence.get(role)
            for role in ("bundle", "transcript", "usage", "evaluation")
            if evidence.get(role)
        },
    }


def _explain_arm(arm: str, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    attempted = [run for run in runs if run["disposition"] in ATTEMPTED]
    tokens = [float(run["tokens"]) for run in attempted if run["tokens"] is not None]
    failed = Counter(name for run in attempted for name in run["failed_dimensions"])
    notes: Counter[str] = Counter()
    for run in runs:
        if run["disposition"] not in (SUCCESS, FAILURE):
            notes[run["disposition"]] += 1
        elif run["disposition"] == FAILURE and run["status"] != "succeeded":
            notes[run["status"]] += 1
    return {
        "arm": arm,
        "runs": len(runs),
        "attempted": len(attempted),
        "successes": sum(1 for run in attempted if run["disposition"] == SUCCESS),
        "failed_dimensions": {name: failed[name] for name in _dimension_order(failed)},
        "tokens": {
            "per_task": statistics.fmean(tokens) if tokens else None,
            "measured_n": len(tokens),
            "attempted_n": len(attempted),
        },
        "notes": dict(sorted(notes.items())),
    }


def _dimension_order(names: Iterable[str]) -> list[str]:
    present = set(names)
    return [name for name in DIMENSIONS if name in present] + sorted(present - set(DIMENSIONS))


def render_task_explain(explain: Mapping[str, Any]) -> str:
    lines = [
        f"# Explain: {explain['task_id']} (task_class={explain['task_class']})",
        "",
        f"- Run: `{explain['suite_run_id']}` ({explain['mode']} mode)   Runs of this task: "
        f"{len(explain['runs'])} across {len(explain['arms'])} arms",
        "- Success is passed / attempted runs, as in the bakeoff report; a failed dimension is "
        "one the run's evaluation scored false (`completed` for a run that ended before "
        "delivering).",
        f"- Evidence paths are relative to `{explain['run_root']}`.",
        "",
        _table(
            ["arm", "success", "failed dimension", "tokens/task (measured/n)", "notes"],
            [
                [
                    arm["arm"],
                    f"{arm['successes']}/{arm['attempted']}",
                    _counts(arm["failed_dimensions"]) or "-",
                    _tokens_text(arm["tokens"]),
                    _counts(arm["notes"]) or "-",
                ]
                for arm in explain["arms"]
            ],
        ),
        "",
        "## Runs",
        "",
        _table(
            [
                "run_id",
                "arm",
                "rep",
                "status",
                "disposition",
                "failed dimension",
                "failure reasons",
                "tokens",
                "judge",
                "evidence",
            ],
            [
                [
                    run["run_id"],
                    run["arm"],
                    run["repetition"] if run["repetition"] is not None else "-",
                    run["status"],
                    run["disposition"],
                    ", ".join(run["failed_dimensions"]) or "-",
                    "; ".join(run["failure_reasons"]) or "-",
                    f"{run['tokens']:,}" if run["tokens"] is not None else run["token_status"],
                    run["judge"] or "-",
                    "<br>".join(_link(role, target) for role, target in run["evidence"].items())
                    or "-",
                ]
                for run in explain["runs"]
            ],
        ),
        "",
    ]
    return "\n".join(lines)


def _counts(counts: Mapping[str, int]) -> str:
    return ", ".join(f"{name} ({count})" for name, count in counts.items())


# --------------------------------------------------------------------------
# Published report
# --------------------------------------------------------------------------


def render_published_report(
    report: Mapping[str, Any],
    *,
    aggregates_sha256: str,
    raw_transcripts_included: bool,
    redaction_summary: Mapping[str, Any],
    layout: Mapping[str, str],
) -> str:
    """The shareable headline: every figure beside its run, n, interval, and gaps."""

    run_id = report.get("suite_run_id")
    counts = report.get("run_counts", {})
    dispositions = ", ".join(
        f"{name} {count}" for name, count in counts.get("by_disposition", {}).items()
    )
    by_rule = ", ".join(
        f"{item['rule']} {item['fields']}" for item in redaction_summary.get("by_rule", [])
    )
    lines = [
        f"# WSB Bakeoff: {report.get('suite_id')}@{report.get('suite_version')}, run `{run_id}`",
        "",
        "Dated evidence, not a verdict. Every figure below comes from run "
        f"`{run_id}` and is printed beside its sample size, its 95% interval, and the "
        "telemetry gaps behind it. This directory is the evidence: re-derive the figures, "
        "or dispute them, with",
        "",
        "```bash",
        "sew bakeoff verify <this-directory>",
        "sew bakeoff explain <this-directory> --task <task-id>",
        "```",
        "",
        f"- Run: `{run_id}` ({report.get('mode')} mode, seed {report.get('seed')})   "
        f"Generated: {report.get('generated_at')}",
        f"- Runs: {counts.get('runs')} ({dispositions or 'none'})   Minimum attempted runs "
        f"per cell: {report.get('min_repetitions')}",
        "- Controls: "
        + ", ".join(
            f"`{item['name']}` = `<harness>+{item['provider_id']}`"
            for item in report.get("controls", [])
        )
        + " (same harness, model profile, and task class)",
        f"- Aggregates digest: `{aggregates_sha256}` (what `verify` re-derives)",
        "- Raw transcripts: "
        + (
            "**included by operator opt-in** (credentials still stripped)"
            if raw_transcripts_included
            else "withheld; protocol fields (event types, tool names, models, usage) kept"
        ),
        f"- Redacted fields: {redaction_summary.get('fields', 0)} ({by_rule or 'none'}); "
        f"every one is listed in [{REDACTION_REPORT_NAME}]({layout['redaction_report']})",
        "",
        "## Task completion by arm",
        "",
        "A per-arm roll-up of the task classes that are not under-sampled; it is not a ranking.",
        "",
        _table(
            [
                "arm",
                "run",
                "task classes",
                "success (passed/attempted)",
                "95% CI",
                "tokens/task (measured/n)",
                "tokens/success",
                "$/success (costed/attempted)",
                "telemetry gaps",
            ],
            [
                [
                    row["arm"],
                    run_id,
                    ", ".join(row.get("task_classes", [])) or "none publishable",
                    _rate_text(row),
                    _ci(row["success_ci_95"]),
                    _tokens_text(row["tokens"]),
                    _tokens_per_success_text(row["tokens"], row["graded_n"]),
                    f"{_per_success_text(row['cost'])} "
                    f"({row['cost']['costed_n']}/{row['cost']['attempted_n']})",
                    _gaps_text(row),
                ]
                for row in report.get("headline", [])
            ],
        ),
    ]
    proxy_notes = sorted(
        {
            note
            for run in report.get("runs", [])
            for note in run.get("cost_pricing_notes", [])
            if note.startswith("proxy_rate:")
        }
    )
    if proxy_notes:
        lines.extend(["", "Operator proxy rates:", *(f"- {note}" for note in proxy_notes)])
    for control in report.get("controls", []):
        rows = [
            row
            for row in report.get("deltas", [])
            if row.get("control") == control["name"] and row["task_class"] == ALL_TASK_CLASSES
        ]
        lines.extend(
            [
                "",
                f"## Change vs {control['name']} (`<harness>+{control['provider_id']}`)",
                "",
                "Pooled over the task classes where both the arm and the control are publishable.",
                "",
                _table(
                    [
                        "arm",
                        "control arm",
                        "run",
                        "task classes",
                        "success delta (95% CI)",
                        "n (arm / control)",
                        "token ratio (arm / control)",
                        "telemetry gaps",
                    ],
                    [
                        [
                            row["arm"],
                            row["control_arm"],
                            run_id,
                            ", ".join(row.get("task_classes", [])) or "none",
                            _success_delta_text(row),
                            f"{_side_n(row['subject'])} / {_side_n(row['baseline'])}",
                            _ratio_text(row["token_ratio"]),
                            ", ".join(row["marks"]) or "-",
                        ]
                        for row in rows
                    ],
                ),
            ]
        )
    warnings = report.get("warnings", [])
    lines.extend(
        [
            "",
            "## Telemetry gaps and warnings",
            "",
            *(
                [f"- ⚠ {item['kind']}: {_md_cell(item['message'])}" for item in warnings]
                or ["- none"]
            ),
            "",
            "## In this bundle",
            "",
            _table(
                ["what", "where"],
                [
                    [
                        "full report (every cell, delta, mark, and run)",
                        _link("bakeoff-report.md", layout["report_markdown"]),
                    ],
                    ["report JSON", _link("bakeoff-report.json", layout["report_json"])],
                    [
                        "run records (transcripts, usage, provider calls, evaluations)",
                        _link(layout["run_root"], layout["run_root"]),
                    ],
                    [
                        "task manifests and catalogs",
                        _link(layout["manifests"], layout["manifests"]),
                    ],
                    ["arm declarations", _link(ARMS_NAME, layout["arm_declarations"])],
                    ["price table", _link(PRICE_TABLE_NAME, layout["price_table"])],
                    ["redaction report", _link(REDACTION_REPORT_NAME, layout["redaction_report"])],
                    ["file inventory with sha256", _link(MANIFEST_NAME, MANIFEST_NAME)],
                ],
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _gaps_text(row: Mapping[str, Any]) -> str:
    parts = [_marks_text(row["marks"])] if row["marks"] else []
    parts += [
        f"{task_class} excluded ({', '.join(reasons)})"
        for task_class, reasons in row.get("excluded_task_classes", {}).items()
    ]
    return "; ".join(parts) or "-"


def _side_n(side: Mapping[str, Any] | None) -> str:
    return "missing" if side is None else str(side["n"])


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _price_table(path: Path) -> Any:
    try:
        return load_price_table(path)
    except (OSError, PriceTableError, yaml.YAMLError) as exc:
        raise BundleError(f"cannot load price table {path}: {exc}") from exc


def _read_manifest(bundle_dir: Path) -> dict[str, Any]:
    manifest = _read_json(_bundle_member(bundle_dir, MANIFEST_NAME))
    if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION or not isinstance(
        manifest.get("layout"), dict
    ):
        raise BundleError(f"{bundle_dir / MANIFEST_NAME} is not a version-1 bundle manifest")
    for key in (
        "run_root",
        "manifests",
        "price_table",
        "arm_declarations",
        "report_json",
        "report_markdown",
        "redaction_report",
        "published_report",
    ):
        try:
            validate_artifact_path(manifest["layout"].get(key), f"bundle manifest layout.{key}")
        except SchemaError as exc:
            raise BundleError(str(exc)) from exc
        _bundle_member(bundle_dir, manifest["layout"][key])
    return manifest


def _bundle_member(bundle_dir: Path, relative: str) -> Path:
    """Reject layout paths that leave a bundle or traverse a symlink within it."""

    root = bundle_dir.absolute()
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise BundleError(f"bundle path escapes publication: {relative}")
    for component in (path, *path.parents):
        if component == root:
            break
        if component.is_symlink():
            raise BundleError(f"bundle path uses a symlink: {relative}")
    return path


def _reject_bundle_symlinks(bundle_dir: Path) -> None:
    """A report reader must never follow an evidence symlink outside the bundle."""

    for path in bundle_dir.rglob("*"):
        if path.is_symlink():
            raise BundleError(f"bundle contains symlink: {path.relative_to(bundle_dir)}")


def _read_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, (dict, list)):
        raise BundleError(f"{path} must contain an object or an array")
    return value


def _read_optional(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _files(root: Path) -> list[tuple[str, Path]]:
    return [
        (path.relative_to(root).as_posix(), path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _component(value: Any) -> str | None:
    """``value`` if it is one safe path component, else None."""

    if not isinstance(value, str) or "/" in value or "\\" in value:
        return None
    try:
        return validate_artifact_path(value, "path component")
    except SchemaError:
        return None


__all__ = [
    "BundleArtifacts",
    "BundleError",
    "MANIFEST_NAME",
    "REDACTION_REPORT_NAME",
    "PUBLISHED_REPORT_NAME",
    "ResolvedRun",
    "aggregate_view",
    "assemble_bundle",
    "differences",
    "explain_task",
    "known_credentials",
    "render_published_report",
    "render_task_explain",
    "resolve_run",
    "sweep_bundle",
    "verify_bundle",
    "write_archive",
]
