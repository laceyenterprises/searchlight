"""Grade captured live cells without changing the arm's usage or cost."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .catalog import module_root
from .evaluator import evaluate_run, load_evaluation_input
from .judge import ArmIdentity, Judge, judge_deliverable, resolve_rubric
from .metrics import _source_use_metrics
from .production_catalog import load_production_catalog, score_deliverable
from .production_scoring import deliverable_schema_valid
from .runner import atomic_write_json
from .schema import SchemaError, load_document, validate_evaluation_record, validate_metrics_record


def grader_for(run: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Resolve a task from its recorded source, then fall back by task id."""
    task_id = str(run["task_id"])
    if run.get("task_source") == "gap":
        from .gap.catalog import load_gap_tasks

        task = load_gap_tasks().get(task_id)
        if task is None or task["task_type"] != "code":
            raise SchemaError(f"unknown GAP code task: {task_id}")
        return "execution", task, None
    legacy = module_root() / "tasks" / task_id
    if run.get("task_source", "legacy") != "production" and legacy.is_dir():
        task = load_document(legacy / "task.yaml")
        return ("rubric" if "blinded_judge" in task else "deterministic", task, None)
    catalog = load_production_catalog()
    for task in catalog["catalog"]["tasks"]:
        if task["id"] == task_id:
            return (
                "rubric" if "judge_rubric" in task else "deterministic",
                task,
                catalog["rubrics"],
            )
    raise SchemaError(f"unknown live task: {task_id}")


def _has_elision(value: Any) -> bool:
    if isinstance(value, str):
        return "<elided:" in value
    if isinstance(value, dict):
        return value.get("elided") is True or any(_has_elision(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_elision(v) for v in value)
    return False


def _arm_specific(name: str, provider_id: str, exposure_tool: str | None) -> bool:
    """True for tool names that identify an arm: its provider's MCP tools.

    Generic harness built-ins (Read, Grep, WebSearch, ...) are shared by every
    arm of a harness and are ordinary English words, so matching them in a
    payload would leave cells ungraded depending on which tools an arm used.
    """

    if exposure_tool and name == exposure_tool:
        return True
    if name.startswith("mcp__"):
        return True
    return bool(provider_id) and provider_id.lower() in name.lower()


def _identity(run_dir: Path, run: dict[str, Any]) -> ArmIdentity:
    names: set[str] = set()
    provider_id = str(run.get("provider_id") or "")
    metadata_path = run_dir / "artifacts" / "spawn-metadata.json"
    if metadata_path.exists():
        metadata = load_document(metadata_path)
        if isinstance(metadata, dict):
            candidates: set[str] = set()
            for key in ("tool_names", "tools"):
                if isinstance(metadata.get(key), list):
                    candidates.update(str(v) for v in metadata[key] if isinstance(v, str))
            exposure = metadata.get("provider_exposure")
            exposure_tool = (
                exposure["tool_name"]
                if isinstance(exposure, dict) and isinstance(exposure.get("tool_name"), str)
                else None
            )
            if exposure_tool:
                candidates.add(exposure_tool)
            audit = metadata.get("arm_audit")
            if isinstance(audit, dict) and isinstance(audit.get("observed_tool_calls"), list):
                candidates.update(
                    str(v) for v in audit["observed_tool_calls"] if isinstance(v, str)
                )
            names = {n for n in candidates if _arm_specific(n, provider_id, exposure_tool)}
            model_id = metadata.get("model_id")
        else:
            model_id = None
    else:
        model_id = None
    arm = str(run.get("arm_id") or f"{run.get('harness_id')}+{run.get('provider_id')}")
    # "default" names no arm: every harness has that profile, and the word is
    # common enough that deliverables using it failed blinding (2026-09-29).
    profile = str(run.get("model_profile") or "")
    return ArmIdentity(
        arm,
        str(run.get("harness_id") or ""),
        str(run.get("provider_id") or ""),
        str(model_id or ("" if profile.strip().lower() == "default" else profile.strip())),
        tuple(sorted(names)),
    )


def _evaluation(
    run: dict[str, Any],
    *,
    outcome: str,
    kind: str,
    dimensions: dict[str, bool],
    reasons: list[str],
    rubric_ref: str | None = None,
) -> dict[str, Any]:
    judge = {"kind": kind}
    if rubric_ref:
        judge["rubric_ref"] = rubric_ref
    return validate_evaluation_record(
        {
            "schema_version": 1,
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "outcome": outcome,
            "dimensions": dimensions,
            "failure_reasons": reasons,
            "judge": judge,
            "evidence_refs": [],
        },
        expected_run_id=run["run_id"],
    )


def grade_run(
    run_dir: Path,
    *,
    judges: Sequence[Judge] = (),
    regrade: bool = False,
    dry_run: bool = False,
    wheelhouse: Path | None = None,
) -> dict[str, Any]:
    """Grade one captured cell; failures to obtain a verdict remain ungraded."""
    run_dir = Path(run_dir)
    run = load_document(run_dir / "run.json")
    current = load_document(run_dir / "evaluations" / "evaluation.json")
    if run["status"] != "succeeded":
        return {
            "run_id": run["run_id"],
            "task_id": run["task_id"],
            "grader": "skipped",
            "outcome": current["outcome"],
        }
    grader, task, rubrics = grader_for(run)
    result = {
        "run_id": run["run_id"],
        "task_id": run["task_id"],
        "grader": grader,
        "outcome": current["outcome"],
    }
    if current["outcome"] in {"pass", "fail"} and not regrade:
        return result
    if dry_run:
        return {**result, "outcome": "would_grade"}
    suite_root = next(
        (parent for parent in run_dir.parents if (parent / "runner-state.json").is_file()), None
    )
    if suite_root and any((suite_root / "publication").glob("*/bundle-manifest.json")):
        raise SchemaError(
            "this run is in a published WSB bundle; bundles are never overwritten, so remove "
            "that publication bundle first, then grade, then rebuild it with `hq-sew bakeoff bundle`"
        )
    # A failed regrade must replace a stale verdict, never preserve it.
    dimensions = {
        name: False
        for name in ("completed", "correct", "grounded", "fresh", "schema_valid", "safe")
    }
    dimensions["completed"] = True
    judge_record = None
    held: dict[str, Any] = {}
    try:
        if grader == "execution":
            from .gap.verify import verify

            diff_path = run_dir / "artifacts/workspace.diff"
            if not diff_path.is_file():
                raise FileNotFoundError("deliverable_missing")
            execution = verify(task, diff_path, wheelhouse=wheelhouse)
            if execution["outcome"] == "not_applicable":
                raise SchemaError("sandbox_unavailable")
            passed = execution["outcome"] == "pass"
            dimensions.update(
                correct=passed, grounded=passed, fresh=passed, schema_valid=True, safe=True
            )
            evaluation = _evaluation(
                run,
                outcome=execution["outcome"],
                kind="execution",
                dimensions=dimensions,
                reasons=[] if passed else ["execution_failed"],
            )
            evaluation["execution"] = execution
            validate_evaluation_record(evaluation, expected_run_id=run["run_id"])
        else:
            answer_path = run_dir / "artifacts" / "final-answer.json"
            if not answer_path.is_file():
                raise FileNotFoundError("deliverable_missing")
            answer = load_document(answer_path)
            # Only a truncated deliverable is ungraded. An empty object or list is
            # something the arm actually returned, so it is scored (and fails).
            if answer is None:
                raise FileNotFoundError("deliverable_missing")
            if _has_elision(answer):
                raise ValueError("deliverable_elided")
            if rubrics is not None and grader == "deterministic":
                scored = score_deliverable(task, answer)
                passed = scored["passed"]
                dimensions.update(
                    correct=passed,
                    grounded=passed,
                    fresh=passed,
                    schema_valid=scored["schema_valid"],
                    safe=True,
                )
                reasons = [
                    f"field:{name}:{','.join(detail['reasons']) or detail['state']}"
                    for name, detail in scored["field_detail"].items()
                    if detail["correct"] is False
                ]
                if not scored["schema_valid"]:
                    reasons.append("schema_invalid")
                if not passed and not reasons:
                    reasons.append("deterministic_threshold")
                # Non-failing observations (e.g. an http citation accepted as https)
                # stay visible on the cell without changing its verdict.
                reasons.extend(
                    f"note:{note}:{name}"
                    for name, detail in scored["field_detail"].items()
                    for note in detail.get("notes", [])
                )
                evaluation = _evaluation(
                    run,
                    outcome="pass" if passed else "fail",
                    kind="deterministic",
                    dimensions=dimensions,
                    reasons=reasons,
                )
            elif grader == "deterministic":
                data = load_evaluation_input(run_dir, module_root() / "tasks" / run["task_id"])
                evaluation = evaluate_run(data)
            else:
                if not judges:
                    raise ValueError("judge_error")
                identity = _identity(run_dir, run)
                if rubrics is None:
                    evaluation = _grade_legacy_rubric(
                        run_dir, run, task, answer, judges, identity, held
                    )
                    judge_record = held.get("record")
                else:
                    rubric = resolve_rubric(task, rubrics)
                    judge_record = judge_deliverable(
                        task, rubric, answer, judges=judges, arm=identity
                    )
                    _require_scored(judge_record)
                    schema_valid = deliverable_schema_valid(task, answer)
                    judged = judge_record["passed"]
                    # Production tasks have no safety check in the canonical scorer;
                    # `safe` matches the deterministic production path above.
                    dimensions.update(
                        correct=judged,
                        grounded=judged,
                        fresh=judged,
                        schema_valid=schema_valid,
                        safe=True,
                    )
                    reasons = [
                        f"rubric:{name}:below_minimum" for name in judge_record["failed_dimensions"]
                    ]
                    if not schema_valid:
                        reasons.append("schema_invalid")
                    evaluation = _evaluation(
                        run,
                        outcome="pass" if judged and schema_valid else "fail",
                        kind="rubric",
                        dimensions=dimensions,
                        reasons=reasons,
                        rubric_ref=rubric["rubric_id"],
                    )
    except (OSError, SchemaError, ValueError, KeyError, TypeError) as exc:
        judge_record = judge_record or held.get("record")
        reason = (
            str(exc)
            if str(exc)
            in {
                "deliverable_missing",
                "deliverable_elided",
                "blinding_failed",
                "sandbox_unavailable",
            }
            else "judge_error"
        )
        evaluation = _evaluation(
            run, outcome="not_applicable", kind="pending", dimensions=dimensions, reasons=[reason]
        )
    metrics_path = run_dir / "metrics" / "metrics.json"
    metrics = load_document(metrics_path)
    # Preserve captured arm usage and cost. Only evaluation-derived projections change.
    prior = set(current.get("failure_reasons") or [])
    metrics["failure_categories"] = sorted(
        (set(metrics.get("failure_categories") or []) - prior) | set(evaluation["failure_reasons"])
    )
    if "source_use" in metrics:
        sources = [load_document(path) for path in sorted((run_dir / "sources").glob("*.json"))]
        metrics["source_use"] = _source_use_metrics(sources, evaluation)
        metrics["source_counts"]["cited"] = metrics["source_use"]["cited"]
    validate_metrics_record(metrics, expected_run_id=run["run_id"])
    # evaluation.json is written last and atomically: it is the commit marker a
    # re-run reads, so an interrupted grade leaves the old verdict in place and
    # a rerun repairs the other files instead of trusting a half-written set.
    if judge_record is not None:
        for entry, judge in zip(judge_record.get("judges") or [], judges, strict=False):
            entry["model_id"] = getattr(judge.transport, "model_id", None) or "default"
            entry["token_usage"] = getattr(judge.transport, "usage", None)
            attempts = getattr(judge.transport, "attempts", None)
            if isinstance(attempts, int) and attempts > 1:
                entry["attempts"] = attempts
            error = getattr(judge.transport, "last_error", None)
            if error:
                entry["transport_error"] = error
        atomic_write_json(run_dir / "evaluations" / "judge.json", judge_record)
    else:
        (run_dir / "evaluations" / "judge.json").unlink(missing_ok=True)
    atomic_write_json(metrics_path, metrics)
    atomic_write_json(run_dir / "evaluations" / "evaluation.json", evaluation)
    return {**result, "outcome": evaluation["outcome"]}


def _require_scored(judge_record: dict[str, Any]) -> None:
    if judge_record["status"] != "scored":
        raise ValueError(
            "blinding_failed" if judge_record["status"] == "blinding_failed" else "judge_error"
        )


def _grade_legacy_rubric(
    run_dir: Path,
    run: dict[str, Any],
    task: dict[str, Any],
    answer: Any,
    judges: Sequence[Judge],
    identity: ArmIdentity,
    held: dict[str, Any],
) -> dict[str, Any]:
    """Grade a legacy blinded-judge task through the canonical evaluator.

    ``evaluate_run`` owns every deterministic dimension (schema, safety,
    citations, freshness); the WSB-02 judge only decides ``correct``. The
    judge scores each of the task's own success criteria and the evaluator's
    ``minimum_score`` is applied to the weakest one, so a pass needs every
    criterion at or above the minimum. The judge record lands in ``held`` even
    when no verdict comes back, so ``judge.json`` can show why.
    """

    task_dir = module_root() / "tasks" / str(run["task_id"])
    binding = task["blinded_judge"]
    criteria = [str(item) for item in task.get("success_criteria") or []]
    if not criteria:
        raise SchemaError(f"legacy rubric task {run['task_id']} declares no success_criteria")
    dimension_ids = [f"criterion_{index}" for index in range(1, len(criteria) + 1)]
    rubric = {
        "rubric_id": binding["rubric_ref"],
        "description": (task_dir / binding["rubric_ref"]).read_text(encoding="utf-8")
        + "\n\nScore each dimension against its success criterion:\n"
        + "\n".join(f"- {dim}: {text}" for dim, text in zip(dimension_ids, criteria, strict=True)),
        "dimensions": dimension_ids,
        "score_scale": binding["score_scale"],
        "minimum_score": binding["minimum_score"],
    }
    judge_task = {
        "id": task["task_id"],
        "task_class": task["task_class"],
        "prompt": (task_dir / "prompt.md").read_text(encoding="utf-8"),
        "deliverable_schema": task["expected_output_schema"],
    }

    def adapter(_evaluator_payload: Any) -> dict[str, Any]:
        record = judge_deliverable(judge_task, rubric, answer, judges=judges, arm=identity)
        held["record"] = record
        _require_scored(record)
        return {"score": min(entry["score"] for entry in record["dimensions"].values())}

    data = load_evaluation_input(run_dir, task_dir)
    return evaluate_run(data, judge=adapter)
