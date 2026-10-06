"""Per-model knowledge-gap admission from complete, uncontaminated references."""

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request
from uuid import uuid4
import zlib

from ..catalog import module_root
from ..evaluator import _open_reachability_request, _safe_reachability_target
from ..harness import HarnessRunConfig, run_harness
from ..runner import RETRYABLE_STATUSES, atomic_write_json
from ..schema import SchemaError, load_document
from ..state import default_state_root
from .catalog import load_gap_tasks
from .source_text import looks_like_html, readable_text


class CalibrationError(SchemaError):
    """Incomplete or invalid evidence cannot establish admission."""


def admission(kind, floor, ceiling, *, reps=5, max_floor=0.2, min_ceiling=0.8):
    if type(reps) is not int or reps < 1:
        raise CalibrationError("reps must be a positive integer")
    for value in (max_floor, min_ceiling):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise CalibrationError("thresholds must be finite rates between zero and one")
    if kind not in {"gap", "control"}:
        raise CalibrationError("unknown task kind")
    for cells in (floor, ceiling):
        if len(cells) != reps or any(type(v) is not bool for v in cells):
            raise CalibrationError("admission requires exactly the configured reps for both arms")
    if kind == "control":
        passed = sum(floor) / reps >= min_ceiling
        return passed, "admitted (control)" if passed else "rejected: floor fails (control)"
    if sum(floor) / reps > max_floor:
        return False, "rejected: floor passes (no gap)"
    if sum(ceiling) / reps < min_ceiling:
        return False, "rejected: ceiling fails (unsolvable)"
    return True, "admitted (gap)"


def _outside_tree(path):
    path = Path(path).expanduser().resolve()
    if any((parent / ".git").exists() for parent in (path, *path.parents)):
        raise CalibrationError("calibration state must be outside the tracked repository tree")
    return path


def calibration_path(state_root, harness, model):
    if harness not in {"codex", "claude-code"}:
        raise CalibrationError("unsupported harness")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", model):
        raise CalibrationError("model must be an explicit, safe model identifier")
    return _outside_tree(state_root) / "gap/calibration" / f"{harness}@{model}.json"


def load_calibration(state_root, harness, model):
    return load_document(calibration_path(state_root, harness, model))


def _capture_source(url):
    # A socket timeout only limits idle time. Isolate the entire fetch so DNS,
    # headers and body reads cannot keep grading alive by trickling bytes.
    command = [
        sys.executable,
        "-c",
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from sew.gap.calibrate import _capture_source_direct; "
        "sys.stdout.buffer.write("
        "_capture_source_direct(sys.stdin.buffer.read().decode('utf-8')).encode('utf-8'))",
        str(Path(__file__).resolve().parents[2]),
    ]
    try:
        result = subprocess.run(
            command, input=url.encode("utf-8"), capture_output=True, timeout=30, check=True
        )
    except subprocess.TimeoutExpired as exc:
        # subprocess.run kills and reaps the child before raising: no abandoned
        # fetch threads or partially captured evidence survive the deadline.
        raise TimeoutError("source capture exceeded 30-second deadline") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"source capture failed: {detail or 'worker failed'}") from exc
    return result.stdout.decode("utf-8")


REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
MAX_SOURCE_REDIRECTS = 5


def _capture_source_direct(url):
    """Fetch only inside the disposable capture worker."""
    # Tool-side source capture, never available to the arm. Failures are graded
    # as unsupported by brief_grade; bound each response and request duration.
    # Redirects are followed by hand so every hop passes the same HTTPS and
    # public-address checks: a correct citation of a moved or versioned page
    # (docs ".../enterprise-server@latest/..." answers 302) is not unsupported.
    for _hop in range(MAX_SOURCE_REDIRECTS + 1):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("source must be an HTTPS URL without credentials")
        target = _safe_reachability_target(url)
        if target is None:
            raise ValueError("source must resolve only to public addresses")
        # Reuse SEW's DNS-pinned transport: no proxies or automatic redirects, and
        # TLS still authenticates the original hostname rather than the address.
        request = Request(url, headers={"User-Agent": "Agent-OS-SEW-GAP/0.1"})
        try:
            with _open_reachability_request(request, target, 30) as response:
                return _source_text(response)
        except HTTPError as exc:
            location = exc.headers.get("Location") if exc.code in REDIRECT_CODES else None
            exc.close()
            if not location:
                raise
            url = urljoin(url, location)
    raise ValueError(f"source exceeds {MAX_SOURCE_REDIRECTS} redirects")


SOURCE_CAPTURE_LIMIT = 2_000_000


def _source_text(response):
    data = response.read(SOURCE_CAPTURE_LIMIT + 1)
    if len(data) > SOURCE_CAPTURE_LIMIT:
        raise ValueError("source exceeds capture limit")
    data = _decode_content(data, response.headers.get("Content-Encoding"))
    charset = response.headers.get_content_charset() or "utf-8"
    try:
        text = data.decode(charset, errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    # Judges read prose; markup alone made one docs page 250,000 characters.
    # get_content_type() reports text/plain when the header is absent; only a
    # declared type is authoritative, otherwise the body is sniffed.
    declared = response.headers.get_content_type() if response.headers.get("Content-Type") else None
    if looks_like_html(text, declared):
        return readable_text(text)
    return text


def _decode_content(data, encoding):
    """Undo a gzip or deflate transfer, keeping the capture limit after decoding.

    Some servers compress whatever the request asks for: www.python.org answers
    gzip even to "Accept-Encoding: identity". Undecoded, the judges were sent
    binary noise and every claim citing the page was labelled unsupported.
    """

    encoding = (encoding or "").strip().lower()
    if encoding in ("", "identity"):
        return data
    if encoding not in ("gzip", "x-gzip", "deflate"):
        raise ValueError(f"unsupported source content encoding: {encoding}")
    # gzip framing, zlib-wrapped deflate, then raw deflate (some servers send it).
    for wbits in (31,) if "gzip" in encoding else (15, -15):
        decoder = zlib.decompressobj(wbits)
        try:
            decoded = decoder.decompress(data, SOURCE_CAPTURE_LIMIT + 1)
        except zlib.error:
            continue
        if len(decoded) > SOURCE_CAPTURE_LIMIT or decoder.unconsumed_tail:
            raise ValueError("source exceeds capture limit")
        if not decoder.eof:
            raise ValueError(f"incomplete {encoding} source")
        # Strict single-stream policy: never silently discard another gzip
        # member or trailing garbage that might contain source qualifications.
        if decoder.unused_data:
            raise ValueError(f"unexpected trailing data in {encoding} source")
        return decoded
    raise ValueError(f"corrupt {encoding} source")


def _execute_cell(config, run_root, execute):
    from ..backoff import bounded_exponential_delay

    attempt_run_dirs = []
    excluded_contaminated_run_dirs = []
    for attempt in range(1, 4):
        attempt_config = (
            config
            if attempt == 1
            else replace(config, run_id_override=f"{config.run_id_override}-att{attempt}")
        )
        result = execute(attempt_config, run_root)
        run_dir = Path(result.bundle_dir)
        attempt_run_dirs.append(str(run_dir))
        run = load_document(run_dir / "run.json")
        metadata = load_document(run_dir / "artifacts/spawn-metadata.json")
        audit = metadata.get("arm_audit", {})
        if run["status"] == "contaminated" or audit.get("contaminated") is True:
            excluded_contaminated_run_dirs.append(str(run_dir))
            if attempt == 3:
                raise CalibrationError(
                    f"contamination retry bound exceeded: {config.run_id_override}: "
                    f"after {attempt} attempts (contaminated reference excluded)"
                )
            continue
        if audit.get("contaminated") is not False or metadata.get("model_id") != config.model_id:
            raise CalibrationError("missing clean audit or mismatched model identity")
        if run["status"] == "succeeded":
            return (
                run_dir,
                attempt_run_dirs,
                excluded_contaminated_run_dirs,
                audit.get("denied_network_attempts", 0),
                audit.get("config_neutralized_network_attempts", 0),
            )
        if run["status"] not in RETRYABLE_STATUSES or attempt == 3:
            raise CalibrationError(
                f"incomplete cell refused: {config.run_id_override}: {run['status']} "
                f"after {attempt} attempt(s)"
            )
        time.sleep(bounded_exponential_delay(attempt - 1, base_seconds=1, max_seconds=2))
    raise AssertionError("unreachable")


def _grade(task, run_dir, root, wheelhouse, judges):
    if task["task_type"] == "code":
        from .verify import verify

        record = verify(
            task, run_dir / "artifacts/workspace.diff", root=root, wheelhouse=wheelhouse
        )
    else:
        from .brief_grade import grade_brief, load_brief_rubric
        from ..grading import _identity

        record = grade_brief(
            task,
            load_brief_rubric(task, root / "catalogs/gap"),
            load_document(run_dir / "artifacts/final-answer.json"),
            judges=judges,
            capture_source=_capture_source,
            arm=_identity(run_dir, load_document(run_dir / "run.json")),
        )
    atomic_write_json(run_dir / "evaluations/calibration-outcome.json", record)
    return record


def calibrate(
    *,
    harness,
    model,
    reps=5,
    max_floor=0.2,
    min_ceiling=0.8,
    state_root=None,
    root=None,
    task_ids=None,
    wheelhouse=None,
    export=None,
    harness_auth=None,
    execute=None,
    grader=None,
    judges=None,
    _previous=None,
):
    state_root = default_state_root() if state_root is None else Path(state_root)
    path = calibration_path(state_root, harness, model)
    # Validate options before any harness spawn or state write.
    admission(
        "gap",
        [False] * reps if type(reps) is int and reps > 0 else [],
        [True] * reps if type(reps) is int and reps > 0 else [],
        reps=reps,
        max_floor=max_floor,
        min_ceiling=min_ceiling,
    )
    root = Path(root or module_root()).resolve()
    tasks = load_gap_tasks(root)
    selected = list(tasks) if task_ids is None else list(dict.fromkeys(task_ids))
    previous_entries = {}
    if _previous is not None:
        if _previous.get("harness") != harness or _previous.get("model") == model:
            raise CalibrationError("recalibration requires the same harness and a changed model")
        if (
            _previous.get("catalog_hash")
            != hashlib.sha256((root / "catalogs/gap/tasks.yaml").read_bytes()).hexdigest()
        ):
            raise CalibrationError("catalog changed; run full calibration")
        previous_entries = {t["task_id"]: t for t in _previous["tasks"]}
        selected = [t for t, entry in previous_entries.items() if entry["verdict"] == "admitted"]
    if (not selected and _previous is None) or set(selected) - tasks.keys():
        raise CalibrationError("calibration needs known, nonempty GAP tasks")
    if judges is None and any(tasks[t]["task_type"] == "brief" for t in selected):
        from .judges import default_brief_judges

        judges = default_brief_judges(harness_auth)
    execute = execute or run_harness
    grader = grader or _grade
    digest = hashlib.sha256((root / "catalogs/gap/tasks.yaml").read_bytes()).hexdigest()
    date = datetime.now(timezone.utc).isoformat()
    run_root = path.parent.parent / "calibration-runs" / uuid4().hex
    entries = []
    for task_id in selected:
        task = tasks[task_id]
        cells = {"floor": [], "ceiling": []}
        for arm in ("floor",) if _previous is not None else cells:
            for rep in range(1, reps + 1):
                config = HarnessRunConfig(
                    harness_id=harness,
                    provider_id=arm,
                    task_id=task_id,
                    model_id=model,
                    model_id_origin="explicit",
                    mode="live",
                    suite_id="gap-calibration",
                    task_source="gap",
                    native_search_available=False,
                    workspace_profile=task["task_type"] == "code",
                    gap_module_root=root,
                    wheelhouse=wheelhouse,
                    harness_auth=harness_auth,
                    run_id_override=f"{task_id}-{arm}-{rep}",
                )
                (
                    run_dir,
                    attempt_run_dirs,
                    excluded_dirs,
                    denied_attempts,
                    neutralized_attempts,
                ) = _execute_cell(config, run_root, execute)
                graded = grader(task, run_dir, root, wheelhouse, judges or [])
                if graded.get("errors"):
                    raise CalibrationError("verifier infrastructure error refused")
                if graded.get("outcome") not in {"pass", "fail"}:
                    raise CalibrationError("ungraded cell refused")
                cells[arm].append(
                    {
                        "rep": rep,
                        "run_dir": str(run_dir),
                        "outcome": graded["outcome"],
                        "attempts": len(attempt_run_dirs),
                        "attempt_run_dirs": attempt_run_dirs,
                        "excluded_contaminated_run_dirs": excluded_dirs,
                        "denied_network_attempts": denied_attempts,
                        "config_neutralized_network_attempts": neutralized_attempts,
                    }
                )
        floor_rate = sum(c["outcome"] == "pass" for c in cells["floor"]) / max(reps, 1)
        retired = False
        if _previous is None:
            admitted, reason = admission(
                task["kind"],
                [c["outcome"] == "pass" for c in cells["floor"]],
                [c["outcome"] == "pass" for c in cells["ceiling"]],
                reps=reps,
                max_floor=max_floor,
                min_ceiling=min_ceiling,
            )
        elif task["kind"] == "gap":
            retired = floor_rate > max_floor
            admitted = not retired
            reason = (
                f"retired: floor rate {floor_rate:g} exceeds {max_floor:g} after model change "
                f"{_previous['model']} -> {model}"
                if retired
                else "admitted (gap): floor freshness check"
            )
        else:
            admitted = floor_rate >= min_ceiling
            reason = "admitted (control)" if admitted else "rejected: floor fails (control)"
        entries.append(
            {
                "task_id": task_id,
                "family": task["family"],
                "kind": task["kind"],
                "date": date,
                "catalog_hash": digest,
                "verdict": "retired" if retired else "admitted" if admitted else "rejected",
                "reason": reason,
                **{
                    arm: {
                        "passes": sum(c["outcome"] == "pass" for c in records),
                        "reps": reps,
                        "rate": sum(c["outcome"] == "pass" for c in records) / reps,
                        "cells": records,
                    }
                    for arm, records in cells.items()
                },
            }
        )
        if _previous is not None:
            entries[-1]["ceiling"] = previous_entries[task_id]["ceiling"]
            entries[-1]["previous_evidence"] = previous_entries[task_id]
            if retired:
                entries[-1]["retired_at"] = date
    if _previous is not None:
        entries.extend(t for t in _previous["tasks"] if t["verdict"] != "admitted")
    if hashlib.sha256((root / "catalogs/gap/tasks.yaml").read_bytes()).hexdigest() != digest:
        raise CalibrationError("catalog changed during calibration")
    record = {
        "record_version": "gap-calibration-v1",
        "harness": harness,
        "model": model,
        "key": f"{harness}@{model}",
        "date": date,
        "catalog_hash": digest,
        "reps": reps,
        "thresholds": {"max_floor": max_floor, "min_ceiling": min_ceiling},
        "tasks": entries,
    }
    record["excluded_contaminated_attempts"] = sum(
        len(cell.get("excluded_contaminated_run_dirs", []))
        for task in entries
        for arm in ("floor", "ceiling")
        for cell in task[arm]["cells"]
    )
    if _previous is not None:
        record["recalibrated_from"] = {"key": _previous["key"], "date": _previous["date"]}
        record["ceiling_basis"] = "inherited reference evidence; floor-only freshness check"
    atomic_write_json(path, record)
    if export is not None:
        atomic_write_json(Path(export), record)
    return record, path


def render_calibration(record):
    rows = [["task", "family", "floor", "ceiling", "verdict"]]
    for task in record["tasks"]:
        rows.append(
            [
                task["task_id"],
                task["family"],
                *[f"{task[a]['passes']}/{task[a]['reps']}" for a in ("floor", "ceiling")],
                task["reason"],
            ]
        )
    widths = [max(len(row[i]) for row in rows) for i in range(4)]
    table = ["  ".join([*(row[i].ljust(widths[i]) for i in range(4)), row[4]]) for row in rows]
    count = sum(t["verdict"] == "admitted" for t in record["tasks"])
    retired = sum(t["verdict"] == "retired" for t in record["tasks"])
    table.append(
        f"calibration {record['key']} {record['date'][:10]}: {count} admitted, {len(record['tasks']) - count - retired} rejected, {retired} retired"
    )
    for field, label in (
        ("denied_network_attempts", "Denied network attempts"),
        ("config_neutralized_network_attempts", "Config-neutralized network attempts"),
    ):
        total = sum(
            cell.get(field, 0)
            for task in record["tasks"]
            for arm in ("floor", "ceiling")
            for cell in task[arm]["cells"]
        )
        table.append(f"{label}: {total}")
    table.append(
        f"Excluded contaminated attempts: {record.get('excluded_contaminated_attempts', 0)}"
    )
    return "\n".join(table)


def recalibrate(*, harness, model, previous_model, state_root=None, **kwargs):
    state_root = default_state_root() if state_root is None else Path(state_root)
    previous = load_calibration(state_root, harness, previous_model)
    thresholds = previous["thresholds"]
    return calibrate(
        harness=harness,
        model=model,
        state_root=state_root,
        _previous=previous,
        max_floor=thresholds["max_floor"],
        min_ceiling=thresholds["min_ceiling"],
        **kwargs,
    )
