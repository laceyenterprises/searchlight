"""Admitted GAP matrices, calibration-based projections and resumable execution."""

import fcntl
import hashlib
from pathlib import Path
import statistics
import time
from uuid import uuid4

from ..arms import PROVIDER_SERVER_NAMES
from ..bakeoff_report import _priceable_usage
from ..catalog import module_root
from ..cost_model import load_price_table, model_cost
from ..harness import HarnessRunConfig, provider_availability_eligible, run_harness
from ..mcp_meter import provider_available
from ..report import _total_tokens
from ..runner import (
    PROVIDER_UNAVAILABLE_MAX_DEFERRALS,
    PROVIDER_UNAVAILABLE_STOP_STREAK,
    RunnerError,
    atomic_write_json,
)
from ..schema import SchemaError, load_document
from ..state import default_state_root
from .calibrate import _grade, _outside_tree, load_calibration
from .catalog import load_gap_tasks


ARMS = frozenset({"floor", "ceiling", "native", *PROVIDER_SERVER_NAMES})


def _read(path):
    try:
        return load_document(Path(path))
    except (OSError, ValueError) as exc:
        raise RunnerError(f"GAP evidence unavailable: {path}: {exc}") from exc


def _usage(run_dir):
    run = _read(Path(run_dir) / "run.json")
    return _read(Path(run_dir) / run.get("metrics_ref", "metrics/metrics.json")).get(
        "token_usage", {}
    )


def project(tasks, calibration, arms, reps, table):
    """Reference means are a proxy for search arms, never measured search cost."""
    rows = []
    for task in tasks:
        entry = next(t for t in calibration["tasks"] if t["task_id"] == task)
        for arm in arms:
            references = [arm] if arm in {"floor", "ceiling"} else ["floor", "ceiling"]
            totals, costs, missing = [], [], []
            for reference in references:
                for cell in entry.get(reference, {}).get("cells", []):
                    token_sum = cost_sum = 0
                    complete = True
                    for directory in cell.get("attempt_run_dirs") or [cell["run_dir"]]:
                        try:
                            usage = _usage(directory)
                            total = _total_tokens(usage)
                            priced, reason = _priceable_usage(usage)
                            if usage.get("accounting_source") != "measured" or total is None:
                                raise RunnerError("usage is not measured")
                            token_sum += total
                            component = (
                                model_cost(calibration["model"], priced, table)
                                if not reason
                                else {}
                            )
                            amount = component.get("amount_usd")
                            if amount is None:
                                missing.append(
                                    component.get("unknown_reason")
                                    or reason
                                    or "model price unknown"
                                )
                                cost_sum = None
                            elif cost_sum is not None:
                                cost_sum += amount
                        except RunnerError as exc:
                            missing.append(str(exc))
                            complete = False
                    if complete:
                        totals.append(token_sum)
                        if cost_sum is not None:
                            costs.append(cost_sum)
            expected = sum(len(entry.get(r, {}).get("cells", [])) for r in references)
            tokens = (
                statistics.fmean(totals) * reps if expected and len(totals) == expected else None
            )
            usd = statistics.fmean(costs) * reps if expected and len(costs) == expected else None
            rows.append(
                dict(
                    task_id=task,
                    arm=arm,
                    cells=reps,
                    projected_tokens=tokens,
                    projected_model_usd=usd,
                    exclusions=missing,
                )
            )
    return dict(
        cells=sum(r["cells"] for r in rows),
        projected_tokens=sum(r["projected_tokens"] for r in rows)
        if all(r["projected_tokens"] is not None for r in rows)
        else None,
        projected_model_usd=sum(r["projected_model_usd"] for r in rows)
        if all(r["projected_model_usd"] is not None for r in rows)
        else None,
        pricing_lower_bound=True,
        basis="calibration reference means (including retries); search arms use pooled floor/ceiling proxy; provider fees, canaries and judges excluded",
        rows=rows,
    )


def run(
    *,
    harness,
    model,
    arms,
    reps=3,
    task_ids=None,
    state_root=None,
    root=None,
    run_root=None,
    resume=False,
    rerun_unavailable=False,
    prewarm_providers=False,
    dry_run=False,
    wheelhouse=None,
    provider_exposures=None,
    harness_auth=None,
    execute=None,
    grader=None,
    judges=None,
    price_table=None,
):
    state_root = Path(state_root or default_state_root())
    if rerun_unavailable and not resume:
        raise RunnerError("GAP --rerun-unavailable requires --resume")
    root = Path(root or module_root()).resolve()
    if type(reps) is not int or reps < 1:
        raise RunnerError("GAP reps must be a positive integer")
    arms = list(dict.fromkeys(arms))
    if not arms or set(arms) - ARMS:
        raise RunnerError("GAP needs known, nonempty arms")
    try:
        calibration = load_calibration(state_root, harness, model)
    except (OSError, SchemaError) as exc:
        raise RunnerError("GAP requires calibration for this harness and model") from exc
    digest = hashlib.sha256((root / "catalogs/gap/tasks.yaml").read_bytes()).hexdigest()
    if (calibration.get("harness"), calibration.get("model"), calibration.get("catalog_hash")) != (
        harness,
        model,
        digest,
    ):
        raise RunnerError("GAP calibration identity or catalog hash mismatch; recalibrate")
    catalog = load_gap_tasks(root)
    entries = {t["task_id"]: t for t in calibration["tasks"]}
    selected = (
        list(dict.fromkeys(task_ids))
        if task_ids is not None
        else [t for t, entry in entries.items() if entry.get("verdict") == "admitted"]
    )
    if not selected or any(
        t not in catalog
        or t not in entries
        or entries[t].get("verdict") != "admitted"
        or entries[t].get("catalog_hash") != digest
        for t in selected
    ):
        raise RunnerError("GAP refuses tasks not admitted for this harness, model and catalog")
    projection = project(selected, calibration, arms, reps, price_table or load_price_table())
    if dry_run:
        return projection, None
    exposures = provider_exposures or {}
    if (set(arms) & PROVIDER_SERVER_NAMES.keys()) - exposures.keys():
        raise RunnerError("GAP provider arms require --provider-mcp-config exposures")
    destination = _outside_tree(run_root or state_root / "gap/runs" / uuid4().hex)
    if resume and not (destination / "runner-state.json").is_file():
        raise RunnerError("GAP --resume requires an existing run root")
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / ".runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunnerError("GAP run already has an active runner") from exc
        return _run_locked(
            destination,
            harness,
            model,
            arms,
            reps,
            selected,
            calibration,
            catalog,
            projection,
            resume,
            root,
            wheelhouse,
            exposures,
            harness_auth,
            execute or run_harness,
            grader or _grade,
            judges,
            rerun_unavailable,
            prewarm_providers,
        )


def _run_locked(
    destination,
    harness,
    model,
    arms,
    reps,
    selected,
    calibration,
    catalog,
    projection,
    resume,
    root,
    wheelhouse,
    exposures,
    harness_auth,
    execute,
    grader,
    judges,
    rerun_unavailable=False,
    prewarm_providers=False,
):
    path = destination / "runner-state.json"
    matrix = dict(harness=harness, model=model, arms=arms, reps=reps, tasks=selected)
    if path.exists():
        if not resume:
            raise RunnerError("GAP run exists; use --resume")
        state = _read(path)
        if state["matrix"] != matrix or state["calibration"] != calibration:
            raise RunnerError("GAP resume matrix or calibration changed")
    else:
        state = dict(
            schema_version="gap-run-v1",
            matrix=matrix,
            calibration=calibration,
            projection=projection,
            entries={},
            attempts={},
            stopped_reason=None,
            deferred=[],
            unavailable_deferrals={},
            unavailable_streak=[],
        )

    def save():
        # State is authoritative; regenerate the report index after a crash.
        atomic_write_json(path, state)
        atomic_write_json(destination / "run-index.json", list(state["entries"].values()))

    save()
    if prewarm_providers:
        from ..provider_startup import prewarm

        try:
            prewarm({arm: exposures[arm] for arm in arms if arm in exposures})
        except ValueError as exc:
            raise RunnerError(str(exc)) from exc
    rerun_keys = (
        {
            key
            for key, entry in state["entries"].items()
            if entry.get("status") == "provider_unavailable"
        }
        if rerun_unavailable
        else None
    )
    if rerun_unavailable:
        # A new repair pass gets a fresh streak budget; previous attempts and
        # bundles remain on disk. Restrict this invocation to unavailable cells.
        state["unavailable_streak"] = []
    if judges is None and any(catalog[t]["task_type"] == "brief" for t in selected):
        from ..judge import Judge
        from ..judge_transport import HarnessJudgeTransport

        judges = [
            Judge(h, HarnessJudgeTransport(h, harness_auth=harness_auth))
            for h in ("claude-code", "codex")
        ]
    state["stopped_reason"] = None
    for task_id in selected:
        task = catalog[task_id]
        for rep in range(1, reps + 1):
            for arm in arms:
                key = f"{task_id}|{arm}|{rep}"
                previous = state["entries"].get(key, {})
                if rerun_keys is not None and key not in rerun_keys:
                    continue
                if (
                    previous.get("status") not in {None, "cancelled"}
                    and key not in state["deferred"]
                    and (rerun_keys is None or key not in rerun_keys)
                ):
                    continue
                attempt = state["attempts"].get(key, 0) + 1
                state["attempts"][key] = attempt
                save()  # Reserve a fresh bundle name before spawning.
                config = HarnessRunConfig(
                    harness_id=harness,
                    provider_id=arm,
                    task_id=task_id,
                    model_id=model,
                    model_id_origin="explicit",
                    mode="live",
                    task_source="gap",
                    suite_id="gap",
                    native_search_available=arm == "native",
                    external_provider=exposures.get(arm),
                    workspace_profile=task["task_type"] == "code",
                    gap_module_root=root,
                    wheelhouse=wheelhouse,
                    harness_auth=harness_auth,
                    run_id_override=f"{task_id}-{arm}-{rep}-att{attempt}",
                    timeout_seconds=task["budgets"]["max_wall_clock_seconds"],
                    max_total_tokens=task["budgets"]["max_total_tokens"],
                    max_provider_calls=task["budgets"]["max_provider_calls"],
                )
                started = time.monotonic()
                result = execute(config, destination / "bundles")
                elapsed = time.monotonic() - started
                directory = Path(result.bundle_dir)
                record = _read(directory / "run.json")
                status = record["status"]
                metadata = _read(directory / "artifacts/spawn-metadata.json")
                if metadata.get("model_id") != model:
                    raise RunnerError("GAP cell model identity mismatch")
                if metadata.get("arm_audit", {}).get("contaminated") is True:
                    status = "contaminated"
                if (
                    arm in PROVIDER_SERVER_NAMES
                    and provider_availability_eligible(status, record.get("failure_category"))
                    and provider_available(directory, arm, record.get("run_id", directory.name))
                    is False
                ):
                    status = "provider_unavailable"
                    record["status"] = status
                    record["failure_category"] = "provider_tools_unavailable"
                    atomic_write_json(directory / "run.json", record)
                if status == "succeeded":
                    if metadata.get("arm_audit", {}).get("contaminated") is not False:
                        raise RunnerError("GAP cell lacks clean arm audit")
                    usage = _usage(directory)
                    total = _total_tokens(usage)
                    calls = metadata.get("process", {}).get("provider_calls")
                    if (
                        total is not None
                        and total > config.max_total_tokens
                        or calls is not None
                        and calls > config.max_provider_calls
                        or elapsed > config.timeout_seconds
                    ):
                        status = "budget_exhausted"
                    else:
                        outcome = grader(task, directory, root, wheelhouse, judges or [])
                        if outcome.get("errors") or outcome.get("outcome") not in {"pass", "fail"}:
                            raise RunnerError("GAP verifier failed; cell remains resumable")
                        atomic_write_json(directory / "evaluations/gap-outcome.json", outcome)
                if status != record["status"]:
                    record["status"] = status
                    atomic_write_json(directory / "run.json", record)
                state["entries"][key] = dict(
                    task_id=task_id,
                    provider_id=arm,
                    harness_id=harness,
                    model_profile="default",
                    repetition=rep,
                    run_id=config.run_id_override,
                    run_dir=str(directory),
                    status=status,
                    attempt_run_dirs=[*previous.get("attempt_run_dirs", []), str(directory)],
                )
                if key in state["deferred"]:
                    state["deferred"].remove(key)
                deferrals = state["unavailable_deferrals"].get(key, 0)
                if (
                    status == "provider_unavailable"
                    and deferrals < PROVIDER_UNAVAILABLE_MAX_DEFERRALS
                ):
                    state["unavailable_streak"].append(key)
                else:
                    state["unavailable_streak"] = []
                if len(state["unavailable_streak"]) >= PROVIDER_UNAVAILABLE_STOP_STREAK:
                    for deferred in state["unavailable_streak"]:
                        state["deferred"].append(deferred)
                        state["unavailable_deferrals"][deferred] = (
                            state["unavailable_deferrals"].get(deferred, 0) + 1
                        )
                    state["unavailable_streak"] = []
                    state["stopped_reason"] = "provider_unavailable_streak"
                if status == "cancelled":
                    state["stopped_reason"] = "cancelled"
                save()
                if state["stopped_reason"]:
                    return state, destination
    save()
    return state, destination
