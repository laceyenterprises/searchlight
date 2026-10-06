"""Small SEW validation CLI for fixture-mode tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import broker_auth
from .host import HostUnavailable
from .agent_lane import AGENT_ARMS, CONTROL_ARM, TIERS, AgentLaneError
from .agent_lane import run_lane as run_agent_lane
from .bakeoff_bundle import (
    assemble_bundle,
    explain_task,
    render_task_explain,
    resolve_run,
    verify_bundle,
)
from .bakeoff_report import generate_bakeoff_report
from .catalog import module_root, validate_domain_catalog, validate_lighthouse_catalog
from .entity_resolution import (
    load_entity_resolution_suite,
    validate_entity_resolution_suite,
)
from .domain_suites import (
    SuiteError,
    available_domains,
    load_domain_suite,
    resolve_suite_surfaces,
    verify_ground_truth,
)
from .domain_report import DomainReportError, explain_hypothesis, generate_domain_report
from .evaluator import evaluate_run, load_evaluation_input
from .harness import HarnessRunConfig, run_acceptance_fixture_matrix, run_harness
from .pi_driver import PiHarnessDriver
from .production_catalog import (
    load_production_catalog,
    publication_blockers,
    validate_production_catalog,
)
from .report import ReportError, generate_report
from .live_harness import LIVE_ENV
from .grading import grade_run
from .judge import Judge
from .judge_transport import (
    DEFAULT_JUDGE_MAX_TOTAL_TOKENS,
    DEFAULT_JUDGE_TIMEOUT_SECONDS,
    HarnessJudgeTransport,
)
from .retrieval_lane import RETRIEVAL_PROVIDERS, RetrievalLaneError, run_lane
from .runner import (
    LiveCellExecutor,
    OperatorBudgets,
    RunnerError,
    SuiteRunner,
    load_provider_exposures,
)
from .schema import SchemaError, validate_fixture_tree

from .state import default_state_root


def _validate_fixtures(args: argparse.Namespace) -> int:
    root = Path(args.fixture_root) if args.fixture_root else module_root() / "fixtures" / "runs"
    task_ids = validate_lighthouse_catalog(module_root())
    results = validate_fixture_tree(root)
    print(f"state_root={default_state_root()}")
    print(f"catalog=lighthouse tasks={len(task_ids)}")
    for result in results:
        print(
            f"fixture={result.run_id} status={result.status} "
            f"metrics={result.metrics_ref} evaluation={result.evaluation_ref}"
        )
    return 0


def _validate_domain_catalog(args: argparse.Namespace) -> int:
    task_ids = validate_domain_catalog(module_root())
    print(f"catalog=domains tasks={len(task_ids)}")
    return 0


def _validate_entity_resolution_suite(args: argparse.Namespace) -> int:
    instance_ids = validate_entity_resolution_suite(module_root())
    print(f"suite=entity-resolution-v1 instances={len(instance_ids)}")
    for instance in load_entity_resolution_suite(module_root()):
        evaluator = instance.validator_kind or (instance.judge_binding or {}).get("rubric_id")
        # `revalidate` is the operator-facing half: a true here means the
        # instance's ground truth can go stale and DSB-07 must re-verify it
        # before a live publication run, not that the suite is broken.
        print(
            f"instance={instance.instance_id} task={instance.task_id} "
            f"evaluator={evaluator} claimant_outcome={instance.expected_claimant_outcome} "
            f"revalidate={bool(instance.ground_truth.get('revalidate_before_live_run'))}"
        )


def _validate_production_catalog(args: argparse.Namespace) -> int:
    root = module_root()
    task_ids = validate_production_catalog(root)
    catalog = load_production_catalog(root)["catalog"]
    by_class: dict[str, int] = {}
    expected_fail: list[str] = []
    for task in catalog["tasks"]:
        by_class[task["task_class"]] = by_class.get(task["task_class"], 0) + 1
        if task["expected_outcome"] == "expected_fail":
            expected_fail.append(task["id"])
    print(f"catalog=production tasks={len(task_ids)}")
    print("classes=" + " ".join(f"{name}:{count}" for name, count in sorted(by_class.items())))
    print(f"expected_fail={len(expected_fail)} {' '.join(sorted(expected_fail))}")
    # A publication run must not quote accuracy against truth nobody re-checked,
    # so the gate prints the blockers rather than only the green count.
    blockers = publication_blockers(catalog)
    print(f"publication_blockers={len(blockers)}")
    for blocker in blockers:
        print(f"  {blocker['reason']} {blocker['task_id']}: {blocker['detail']}")
    return 0


def _domain_capabilities() -> dict[str, object]:
    from .domain_surfaces import PROVIDERS
    from .providers import make_provider

    return {
        provider_id: make_provider(provider_id, live_enabled=False).capabilities
        for provider_id in sorted(PROVIDERS)
    }


def _validate_domain_suite(args: argparse.Namespace) -> int:
    root = module_root()
    domains = [args.domain] if args.domain else available_domains(root)
    if not domains:
        raise SuiteError("no domain suite manifests found under catalogs/domains/suites")
    capabilities = _domain_capabilities()
    for domain in domains:
        suite = load_domain_suite(domain, root)
        records = resolve_suite_surfaces(suite, capabilities)
        generic = sum(1 for record in records if record["generic_surface"])
        print(
            f"suite={suite.suite_id} domain={suite.domain} hypothesis={suite.hypothesis_id} "
            f"instances={len(suite.instances)} surface_cells={len(records)} "
            f"generic_surface_cells={generic}"
        )
        for instance in suite.instances:
            scoring = (
                f"validator={instance.validator['kind']}"
                if instance.validator
                else f"rubric={instance.rubric['rubric_id']}"
            )
            print(
                f"  instance={instance.instance_id} task={instance.task_id} "
                f"repository={instance.repository} references={len(instance.references)} {scoring}"
            )
        if args.surfaces:
            for record in records:
                print(
                    f"    surface instance={record['instance_id']} "
                    f"provider={record['provider_id']} surface={record['surface_id']} "
                    f"generic={record['generic_surface']} reason={record['downgrade_reason']}"
                )
    return 0


def _verify_domain_ground_truth(args: argparse.Namespace) -> int:
    """Re-resolve a suite's ground truth against the live GitHub API.

    Skip-safe by default: the offline gate is `validate-domain-suite`, and a
    contributor without network access must not see a red check for it.
    """
    if os.environ.get("SEW_GROUND_TRUTH_LIVE") != "1":
        print("ground_truth_verification=skipped reason=SEW_GROUND_TRUTH_LIVE!=1")
        return 0
    suite = load_domain_suite(args.domain, module_root())
    checks = verify_ground_truth(
        suite,
        token=os.environ.get("SEW_GROUND_TRUTH_TOKEN") or os.environ.get("GH_TOKEN"),
        timeout_seconds=args.timeout_seconds,
    )
    for check in checks:
        status = "resolved" if check.resolved else "unresolved"
        print(
            f"{status} instance={check.instance_id} role={check.role} "
            f"detail={check.detail} url={check.url}"
        )
    unresolved = [check for check in checks if not check.resolved]
    print(f"ground_truth_checks={len(checks)} unresolved={len(unresolved)}")
    return 1 if unresolved else 0


def _run_fixture_harnesses(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root) if args.output_root else default_state_root() / "runs"
    results = run_acceptance_fixture_matrix(
        output_root,
        task_id=args.task_id,
        provider_id=args.external_provider,
    )
    for result in results:
        print(
            f"run={result.run_id} status={result.status} "
            f"bundle={result.bundle_dir / result.evidence_bundle_ref} "
            f"tokens={result.token_accounting_source}"
        )
    return 0


def _run_live_harness(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root) if args.output_root else default_state_root() / "live"
    result = run_harness(
        HarnessRunConfig(
            harness_id=args.harness,
            provider_id="native",
            task_id=args.task_id,
            mode="live",
            prompt_text=args.prompt_text,
            model_id=args.model_id,
            timeout_seconds=args.timeout_seconds,
            boot_timeout_seconds=args.boot_timeout_seconds,
            max_total_tokens=args.max_total_tokens,
            harness_auth=args.harness_auth,
        ),
        output_root,
    )
    print(
        f"run={result.run_id} status={result.status} "
        f"category={result.failure_category or '-'} "
        f"bundle={result.bundle_dir / result.evidence_bundle_ref} "
        f"tokens={result.token_accounting_source}"
    )
    return 0 if result.status == "succeeded" else 1


def _evaluate_fixture(args: argparse.Namespace) -> int:
    root = module_root()
    run_dir = Path(args.run_dir)
    task_id = str(args.task_id)
    data = load_evaluation_input(run_dir, root / "tasks" / task_id)
    record = evaluate_run(data)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def _pi_fixture(args: argparse.Namespace) -> int:
    driver = PiHarnessDriver.from_config()
    profile_ids = args.profile or None
    results = driver.run_lighthouse_fixture(
        Path(args.output_root),
        profile_ids=profile_ids,
        provider_id=args.provider,
    )
    print(f"pi_fixture_runs={len(results)} output_root={args.output_root}")
    for result in results:
        print(f"run={result.run_id} status={result.status} dir={result.run_dir}")
    return 0


def _pi_live_smoke(args: argparse.Namespace) -> int:
    driver = PiHarnessDriver.from_config()
    result = driver.run_live_smoke(
        Path(args.output_root),
        profile_id=args.profile,
        provider_id=args.provider,
        task_id=args.task,
    )
    print(f"pi_live_smoke={result.status} reason={result.reason}")
    if result.run_dir is not None:
        print(f"dir={result.run_dir}")
    return 0


def _runner_budgets(args: argparse.Namespace) -> OperatorBudgets | None:
    values = (
        args.max_provider_calls,
        args.max_provider_result_chars,
        args.max_total_tokens,
        args.max_wall_clock_seconds,
    )
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise RunnerError("operator budgets require all four --max-* options")
    return OperatorBudgets(
        max_provider_calls=args.max_provider_calls,
        max_provider_result_chars=args.max_provider_result_chars,
        max_total_tokens=args.max_total_tokens,
        max_wall_clock_seconds=args.max_wall_clock_seconds,
    )


def _run_suite(args: argparse.Namespace) -> int:
    live_executor = None
    if args.mode == "live":
        selected_auth = broker_auth.auth_source(args.harness_auth)
        print(
            f"harness auth source: {selected_auth} "
            f"({'explicit' if args.harness_auth else 'auto-selected'})",
            file=sys.stderr,
        )
        live_executor = LiveCellExecutor(
            provider_exposures=(
                load_provider_exposures(Path(args.provider_mcp_config))
                if args.provider_mcp_config
                else None
            ),
            harness_auth=selected_auth,
        )
    runner = SuiteRunner(
        state_root=Path(args.state_root) if args.state_root else None,
        live_executor=live_executor,
    )
    if args.dry_run:
        result = runner.dry_run(
            args.suite, repetitions=args.repetitions, seed=args.seed, mode=args.mode
        )
    else:
        result = runner.run(
            args.suite,
            mode=args.mode,
            repetitions=args.repetitions,
            seed=args.seed,
            run_id=args.run_id,
            resume=not args.no_resume,
            max_cells=args.max_cells,
            operator_budgets=_runner_budgets(args),
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _report(args: argparse.Namespace) -> int:
    run_root = Path(args.run_root)
    artifacts = generate_report(run_root)
    print(
        json.dumps(
            {
                "json_report": str(artifacts.json_path),
                "markdown_report": str(artifacts.markdown_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _bakeoff_report(args: argparse.Namespace) -> int:
    artifacts = generate_bakeoff_report(
        Path(args.run_root),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        price_table_path=Path(args.price_table) if args.price_table else None,
    )
    print(
        json.dumps(
            {
                "json_report": str(artifacts.json_path),
                "markdown_report": str(artifacts.markdown_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _bakeoff_grade(args: argparse.Namespace) -> int:
    target = Path(args.run_dir).expanduser().resolve()
    if (target / "run.json").is_file():
        run_dirs = [target]
        suite_root = next(
            (parent for parent in target.parents if (parent / "runner-state.json").is_file()),
            None,
        )
    elif (target / "runner-state.json").is_file():
        suite_root = target
        index = json.loads((target / "run-index.json").read_text())
        run_dirs = sorted(
            {
                (
                    Path(entry["run_dir"])
                    if Path(entry["run_dir"]).is_absolute()
                    else target / entry["run_dir"]
                ).resolve()
                for entry in index
                if isinstance(entry.get("run_dir"), str)
            }
        )
    else:
        raise SchemaError("grade expects a run directory or suite run directory")
    if suite_root is not None and any((suite_root / "publication").glob("*/bundle-manifest.json")):
        raise SchemaError(
            "this suite already has a hashed WSB bundle; bundles are never overwritten, so "
            "remove the publication bundle first, grade, then rebuild it with "
            "`sew bakeoff bundle <suite-run-dir>`"
        )
    if not args.dry_run and os.environ.get(LIVE_ENV) != "1":
        raise SchemaError(f"grading spawns a live judge; set {LIVE_ENV}=1")
    judges = []
    selected_auth = (
        broker_auth.auth_source(args.harness_auth)
        if args.judge_harness or args.second_judge_harness
        else None
    )
    if selected_auth:
        print(
            f"harness auth source: {selected_auth} "
            f"({'explicit' if args.harness_auth else 'auto-selected'})",
            file=sys.stderr,
        )
    for harness, model in (
        (args.judge_harness, args.judge_model),
        (args.second_judge_harness, args.second_judge_model),
    ):
        if harness:
            transport = HarnessJudgeTransport(
                harness,
                model,
                timeout_seconds=args.judge_timeout_seconds,
                max_total_tokens=args.judge_max_tokens,
                harness_auth=selected_auth,
            )
            judges.append(Judge(f"{harness}:{model or 'default'}:{len(judges) + 1}", transport))
    unreadable = 0
    for run_dir in run_dirs:
        try:
            row = grade_run(
                run_dir,
                judges=judges,
                regrade=args.regrade,
                dry_run=args.dry_run,
                wheelhouse=getattr(args, "wheelhouse", None),
            )
        except (OSError, SchemaError, ValueError, KeyError, TypeError) as exc:
            # A damaged cell cannot carry a valid evaluation record (it may have no
            # run id), so report it and keep grading the rest of the batch.
            unreadable += 1
            print(f"{run_dir} - unreadable {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print("{run_id} {task_id} {grader} {outcome}".format(**row))
    return 1 if unreadable else 0


def _bakeoff_bundle(args: argparse.Namespace) -> int:
    artifacts = assemble_bundle(
        Path(args.run_root),
        output_dir=Path(args.output_dir) if args.output_dir else None,
        include_raw_transcripts=args.include_raw_transcripts,
        price_table_path=Path(args.price_table) if args.price_table else None,
        archive=not args.no_archive,
    )
    print(
        json.dumps(
            {
                "bundle_dir": str(artifacts.bundle_dir),
                "archive": str(artifacts.archive_path) if artifacts.archive_path else None,
                "published_report": str(artifacts.published_report),
                "redaction_report": str(artifacts.redaction_report),
                "json_report": str(artifacts.report_json),
                "markdown_report": str(artifacts.report_markdown),
                "redacted_fields": artifacts.redacted_fields,
                "raw_transcripts_included": artifacts.raw_transcripts_included,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _bakeoff_verify(args: argparse.Namespace) -> int:
    result = verify_bundle(Path(args.bundle_dir))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def _bakeoff_explain(args: argparse.Namespace) -> int:
    explain = explain_task(
        resolve_run(args.run),
        args.task,
        price_table_path=Path(args.price_table) if args.price_table else None,
    )
    print(render_task_explain(explain), end="")
    return 0


def _domain_report(args: argparse.Namespace) -> int:
    json_path, markdown_path = generate_domain_report(
        Path(args.run), Path(args.output_dir) if args.output_dir else None
    )
    print(json.dumps({"json_report": str(json_path), "markdown_report": str(markdown_path)}))
    return 0


def _domain_explain(args: argparse.Namespace) -> int:
    report_path = Path(args.report)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainReportError(f"cannot read domain report {report_path}: {exc}") from exc
    print(explain_hypothesis(report, args.hypothesis), end="")
    return 0


def _retrieval(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root) if args.output_root else default_state_root() / "retrieval"
    report = run_lane(
        output_root,
        config=args.config,
        repetitions=args.repetitions,
        providers=tuple(args.provider) if args.provider else RETRIEVAL_PROVIDERS,
        query_ids=args.query or None,
        timeout_seconds=args.timeout_seconds,
        max_cells=args.max_cells,
        progress=not args.quiet,
        pace_seconds=args.pace_seconds,
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "run_id",
                    "run_dir",
                    "total_cells",
                    "suspect_ground_truth",
                    "by_provider",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _agent(args: argparse.Namespace) -> int:
    output_root = Path(args.output_root) if args.output_root else default_state_root() / "agent"
    report = run_agent_lane(
        output_root,
        tier=args.tier,
        repetitions=args.repetitions,
        arms=tuple(args.arm) if args.arm else AGENT_ARMS + (CONTROL_ARM,),
        task_ids=args.task or None,
        timeout_seconds=args.timeout_seconds,
        max_cells=args.max_cells,
        progress=not args.quiet,
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "run_id",
                    "run_dir",
                    "total_cells",
                    "suspect_ground_truth",
                    "by_arm",
                )
            },
            indent=2,
            sort_keys=True,
            default=str,
        )
    )
    return 0


def _gap_run(args):
    from .gap.runner import run
    from .cost_model import load_price_table

    record, path = run(
        harness=args.harness,
        model=args.model,
        arms=args.arm,
        reps=args.reps,
        task_ids=args.task,
        state_root=args.state_root,
        run_root=args.run_root,
        resume=args.resume,
        rerun_unavailable=args.rerun_unavailable,
        prewarm_providers=args.prewarm_providers,
        dry_run=args.dry_run,
        wheelhouse=args.wheelhouse,
        harness_auth=args.harness_auth,
        provider_exposures=load_provider_exposures(args.provider_mcp_config)
        if args.provider_mcp_config and not args.dry_run
        else None,
        price_table=load_price_table(args.price_table) if args.price_table else None,
    )
    if args.dry_run:
        print(json.dumps(record, indent=2))
    else:
        print(f"GAP run: {path}; stopped: {record['stopped_reason'] or 'complete'}")
    return 0


def _gap_calibrate(args):
    from .gap.calibrate import calibrate, render_calibration

    record, path = calibrate(
        harness=args.harness,
        model=args.model,
        reps=args.reps,
        max_floor=args.max_floor,
        min_ceiling=args.min_ceiling,
        task_ids=args.task,
        wheelhouse=args.wheelhouse,
        export=args.export,
        harness_auth=args.harness_auth,
    )
    print(render_calibration(record))
    print(f"  -> {path}")
    return 0


def _gap_recalibrate(args):
    from .gap.calibrate import recalibrate, render_calibration

    record, path = recalibrate(
        harness=args.harness,
        model=args.model,
        previous_model=args.previous_model,
        reps=args.reps,
        state_root=args.state_root,
        wheelhouse=args.wheelhouse,
        export=args.export,
        harness_auth=args.harness_auth,
    )
    print(render_calibration(record))
    print(f"  -> {path}")
    return 0


def _gap_refresh(args):
    from .gap.refresh import refresh
    from .gap.mine import DEFAULT_PACKAGES

    record, path = refresh(
        since=args.since,
        packages=args.package or DEFAULT_PACKAGES,
        state_root=args.state_root,
    )
    print(json.dumps({"tickets": len(record["tickets"]), "path": str(path)}, indent=2))
    return 0


def _gap_mine(args):
    from .gap.mine import DEFAULT_PACKAGES, mine

    record = mine(args.package or DEFAULT_PACKAGES, since=args.since)
    target = (
        (Path(args.state_root) if args.state_root else default_state_root())
        / "gap/candidates"
        / (args.since + ".json")
    )
    _write_gap_record(target, record)
    print(json.dumps({"candidates": len(record["candidates"]), "path": str(target)}, indent=2))
    return 0


def _write_gap_record(target, record):
    import tempfile

    if target.resolve().is_relative_to(module_root().resolve()):
        raise SchemaError("GAP state must live outside the tracked SEW module")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as stream:
        staging = Path(stream.name)
        try:
            json.dump(record, stream, indent=2)
            stream.write("\n")
            stream.close()
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)


def _gap_validate_task(args):
    from .gap.catalog import load_gap_tasks
    from .gap.validate import validate_task

    root = Path(args.module_root) if args.module_root else module_root()
    tasks = load_gap_tasks(root)
    if args.task not in tasks:
        raise SchemaError(f"unknown GAP task: {args.task}")
    record = validate_task(tasks[args.task], root=root, wheelhouse=args.wheelhouse)
    target = (
        (Path(args.state_root) if args.state_root else default_state_root())
        / "gap/validation"
        / (args.task + ".json")
    )
    _write_gap_record(target, record)
    print(
        json.dumps(
            {
                "accepted": record["accepted"],
                "reason": record["reason"],
                "path": str(target),
            },
            indent=2,
        )
    )
    return 0 if record["accepted"] else 1


def _gap_report(args):
    from .gap.report import generate_gap_report
    from .cost_model import load_price_table

    artifacts = generate_gap_report(
        Path(args.run_root),
        output_dir=args.output_dir,
        calibration=args.calibration,
        seed=args.seed,
        price_table=load_price_table(Path(args.price_table)) if args.price_table else None,
    )
    print(artifacts.markdown_path.read_text())
    print(f"  -> {artifacts.json_path}")
    return 0


def _gap_explain(args):
    from .gap.report import build_gap_report, explain_gap

    record, markdown = explain_gap(
        build_gap_report(Path(args.run_root), calibration=args.calibration),
        task=args.task,
        arm=args.arm,
    )
    print(json.dumps(record, indent=2) if args.format == "json" else markdown)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sew")
    sub = parser.add_subparsers(dest="command", required=True)
    from .doctor import command as doctor_command

    doctor_parser = sub.add_parser("doctor", help="inspect host mode and local capabilities")
    doctor_parser.add_argument(
        "--codex-broker-auth",
        action="store_true",
        help="mint and validate broker auth without a model turn",
    )
    doctor_parser.set_defaults(func=doctor_command)
    validate = sub.add_parser(
        "validate-fixtures",
        help="validate redacted fixture runs without network access",
    )
    validate.add_argument("--fixture-root", help="override fixture run root")
    validate.set_defaults(func=_validate_fixtures)
    validate_domains = sub.add_parser(
        "validate-domain-catalog",
        help="validate DSB domain task and claim catalogs without network access",
    )
    validate_domains.set_defaults(func=_validate_domain_catalog)
    validate_entities = sub.add_parser(
        "validate-entity-resolution-suite",
        help="validate the DSB-05 entity resolution run instances without network access",
    )
    validate_entities.set_defaults(func=_validate_entity_resolution_suite)
    validate_production = sub.add_parser(
        "validate-production-catalog",
        help="validate the WSB production task catalog and rubrics without network access",
    )
    validate_production.set_defaults(func=_validate_production_catalog)
    validate_suite = sub.add_parser(
        "validate-domain-suite",
        help="validate DSB runnable domain suites and their surface matrix offline",
    )
    validate_suite.add_argument("--domain", help="restrict to one domain suite")
    validate_suite.add_argument(
        "--surfaces",
        action="store_true",
        help="print the per-instance, per-vendor surface matrix",
    )
    validate_suite.set_defaults(func=_validate_domain_suite)
    verify_ground = sub.add_parser(
        "verify-domain-ground-truth",
        help="re-resolve a domain suite's ground truth against the live GitHub API",
    )
    verify_ground.add_argument("--domain", default="code_and_pr")
    verify_ground.add_argument("--timeout-seconds", type=float, default=30.0)
    verify_ground.set_defaults(func=_verify_domain_ground_truth)
    harnesses = sub.add_parser(
        "run-fixture-harnesses",
        help="write Codex/Claude Code native and external-provider fixture bundles",
    )
    harnesses.add_argument("--output-root", help="override output root for generated run bundles")
    harnesses.add_argument("--task-id", default="current-fact-lookup-v1")
    harnesses.add_argument(
        "--external-provider",
        default="exa",
        choices=("exa", "parallel-web", "firecrawl", "brave", "tavily", "perplexity"),
    )
    harnesses.set_defaults(func=_run_fixture_harnesses)
    live_harness = sub.add_parser(
        "run-live-harness",
        help=f"spawn one real Codex/Claude Code cell (operator-gated: {LIVE_ENV}=1)",
    )
    live_harness.add_argument("--harness", required=True, choices=("claude-code", "codex"))
    live_harness.add_argument("--output-root", help="override output root for the run bundle")
    live_harness.add_argument("--task-id", default="current-fact-lookup-v1")
    live_harness.add_argument("--prompt-text", help="send this prompt instead of the task's")
    live_harness.add_argument("--model-id", help="pass --model to the harness")
    live_harness.add_argument("--timeout-seconds", type=float)
    live_harness.add_argument("--boot-timeout-seconds", type=float)
    live_harness.add_argument("--max-total-tokens", type=int)
    live_harness.add_argument("--harness-auth", choices=("broker", "account"))
    live_harness.set_defaults(func=_run_live_harness)
    evaluate = sub.add_parser(
        "evaluate-fixture",
        help="evaluate one fixture run without live network or judge calls",
    )
    evaluate.add_argument("run_dir", help="fixture run directory")
    evaluate.add_argument("--task-id", required=True, help="task manifest id")
    evaluate.set_defaults(func=_evaluate_fixture)
    pi_fixture = sub.add_parser("pi-fixture", help="materialize Pi lighthouse fixture run bundles")
    pi_fixture.add_argument("--output-root", required=True, help="directory for generated runs")
    pi_fixture.add_argument(
        "--profile",
        action="append",
        help="Pi profile id to run; may be repeated; defaults to suite Pi profiles",
    )
    pi_fixture.add_argument(
        "--provider",
        default="fixture",
        help="provider id to expose in generated fixture runs",
    )
    pi_fixture.set_defaults(func=_pi_fixture)
    pi_live = sub.add_parser("pi-live-smoke", help="skip-safe Pi live smoke probe")
    pi_live.add_argument(
        "--output-root", required=True, help="directory for generated smoke record"
    )
    pi_live.add_argument("--profile", default="oss-small", help="Pi profile id")
    pi_live.add_argument("--provider", default="exa", help="provider id")
    pi_live.add_argument("--task", default="current-fact-lookup-v1", help="task id")
    pi_live.set_defaults(func=_pi_live_smoke)
    run = sub.add_parser("run", help="expand and execute a manifest-defined suite")
    run.add_argument("--suite", default="lighthouse", help="suite id under catalogs/")
    run.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    run.add_argument("--dry-run", action="store_true", help="print seeded matrix without running")
    run.add_argument("--repetitions", type=int, help="override suite repetitions")
    run.add_argument("--seed", type=int, help="override suite randomization seed")
    run.add_argument("--run-id", help="resume state id under the SEW state root")
    run.add_argument("--state-root", help="override SEW state root")
    run.add_argument("--no-resume", action="store_true", help="start a fresh state file")
    run.add_argument("--max-cells", type=int, help="execute at most N new cells this invocation")
    run.add_argument("--max-provider-calls", type=int)
    run.add_argument("--max-provider-result-chars", type=int)
    run.add_argument("--max-total-tokens", type=int)
    run.add_argument("--max-wall-clock-seconds", type=int)
    run.add_argument(
        "--provider-mcp-config",
        help="live mode: YAML/JSON map of provider id to the MCP server its arm exposes",
    )
    run.add_argument("--harness-auth", choices=("broker", "account"))
    run.set_defaults(func=_run_suite)
    retrieval = sub.add_parser(
        "retrieval", help="run the live provider-vs-provider retrieval comparison"
    )
    retrieval.add_argument("--output-root", help="directory for run artifacts")
    retrieval.add_argument(
        "--config", default="matched", choices=("matched", "exa-text-view", "default")
    )
    retrieval.add_argument("--repetitions", type=int, default=3)
    retrieval.add_argument(
        "--provider",
        action="append",
        choices=RETRIEVAL_PROVIDERS,
        help="restrict to one provider; may be repeated",
    )
    retrieval.add_argument("--query", action="append", help="restrict to query ids; repeatable")
    retrieval.add_argument("--timeout-seconds", type=float, default=90.0)
    retrieval.add_argument("--max-cells", type=int, help="cap cells for a smoke run")
    retrieval.add_argument("--quiet", action="store_true", help="suppress per-cell progress")
    retrieval.add_argument(
        "--pace-seconds",
        type=float,
        default=0.0,
        help="sleep between cells to stay under provider rate limits",
    )
    retrieval.set_defaults(func=_retrieval)
    agent = sub.add_parser(
        "agent", help="run the live vendor-agent comparison against a DIY control"
    )
    agent.add_argument("--output-root", help="directory for run artifacts")
    agent.add_argument("--tier", default="mid", choices=sorted(TIERS))
    agent.add_argument("--repetitions", type=int, default=2)
    agent.add_argument(
        "--arm",
        action="append",
        choices=AGENT_ARMS + (CONTROL_ARM,),
        help="restrict to one arm; may be repeated",
    )
    agent.add_argument("--task", action="append", help="restrict to task ids; repeatable")
    agent.add_argument("--timeout-seconds", type=float, default=600.0)
    agent.add_argument("--max-cells", type=int, help="cap cells for a smoke run")
    agent.add_argument("--quiet", action="store_true", help="suppress per-cell progress")
    agent.set_defaults(func=_agent)
    report = sub.add_parser("report", help="generate Markdown and JSON reports for a suite run")
    report.add_argument("run_root", help="suite run root containing runner-state.json")
    report.set_defaults(func=_report)
    gap = sub.add_parser("gap", help="Search Gap Bench knowledge-gap calibration")
    gap_sub = gap.add_subparsers(dest="gap_command", required=True)
    gap_run = gap_sub.add_parser("run", help="run admitted GAP tasks or project calibration spend")
    gap_run.add_argument("--harness", required=True, choices=("claude-code", "codex"))
    gap_run.add_argument("--model", required=True)
    gap_run.add_argument("--arm", action="append", required=True, help="arm ID; repeatable")
    gap_run.add_argument("--task", action="append", help="admitted task ID; repeatable")
    gap_run.add_argument("--reps", type=int, default=3)
    gap_run.add_argument("--state-root", type=Path)
    gap_run.add_argument(
        "--run-root",
        type=Path,
        help="persistent suite directory outside the repository",
    )
    gap_run.add_argument("--resume", action="store_true")
    gap_run.add_argument(
        "--rerun-unavailable",
        action="store_true",
        help="with --resume, rerun only unavailable cells with fresh bundles",
    )
    gap_run.add_argument(
        "--prewarm-providers",
        action="store_true",
        help="resolve exact pinned npx packages before the battery",
    )
    gap_run.add_argument(
        "--dry-run",
        action="store_true",
        help="project cells, tokens and model USD; no spawns or key reads",
    )
    gap_run.add_argument("--wheelhouse", type=Path)
    gap_run.add_argument("--provider-mcp-config", type=Path)
    gap_run.add_argument("--harness-auth", choices=("broker", "account"))
    gap_run.add_argument("--price-table", type=Path)
    gap_run.set_defaults(func=_gap_run)
    calibration = gap_sub.add_parser("calibrate", help="run floor and ceiling admission references")
    calibration.add_argument("--harness", required=True, choices=("claude-code", "codex"))
    calibration.add_argument(
        "--model", required=True, help="explicit model ID for per-model admission"
    )
    calibration.add_argument("--reps", type=int, default=5)
    calibration.add_argument(
        "--max-floor", type=float, default=0.2, help="maximum gap floor pass rate"
    )
    calibration.add_argument(
        "--min-ceiling",
        type=float,
        default=0.8,
        help="minimum ceiling and control floor pass rate",
    )
    calibration.add_argument("--task", action="append", help="restrict to task IDs; repeatable")
    calibration.add_argument("--wheelhouse", type=Path)
    calibration.add_argument("--export", type=Path, help="explicit reviewed-record export path")
    calibration.add_argument("--harness-auth", choices=("broker", "account"))
    calibration.set_defaults(func=_gap_calibrate)
    mine = gap_sub.add_parser("mine", help="rank post-cutoff PyPI releases with source receipts")
    mine.add_argument("--ecosystem", required=True, choices=("pypi",))
    mine.add_argument("--since", required=True, help="ISO cutoff date (exclusive)")
    mine.add_argument(
        "--package",
        action="append",
        help="PyPI project; repeatable (default: requests, urllib3, httpx, pydantic, pandas)",
    )
    mine.add_argument("--state-root", help="override SEW state root")
    mine.set_defaults(func=_gap_mine)
    refresh = gap_sub.add_parser("refresh", help="mine candidates and emit authoring tickets")
    refresh.add_argument("--ecosystem", required=True, choices=("pypi",))
    refresh.add_argument("--since", required=True)
    refresh.add_argument("--package", action="append")
    refresh.add_argument("--state-root", type=Path)
    refresh.set_defaults(func=_gap_refresh)
    recalibration = gap_sub.add_parser(
        "recalibrate", help="check admitted floors after a model change"
    )
    recalibration.add_argument("--harness", required=True, choices=("claude-code", "codex"))
    recalibration.add_argument("--model", required=True)
    recalibration.add_argument("--previous-model", required=True)
    recalibration.add_argument("--reps", type=int, default=5)
    recalibration.add_argument("--state-root", type=Path)
    recalibration.add_argument("--wheelhouse", type=Path)
    recalibration.add_argument("--export", type=Path)
    recalibration.add_argument("--harness-auth", choices=("broker", "account"))
    recalibration.set_defaults(func=_gap_recalibrate)
    validity = gap_sub.add_parser(
        "validate-task", help="repeat offline kind-specific validity checks three times"
    )
    validity.add_argument("task", help="GAP catalog task id")
    validity.add_argument("--wheelhouse", type=Path, help="offline cache of catalog-pinned wheels")
    validity.add_argument(
        "--module-root", help="SEW module root containing catalogs/gap/tasks.yaml"
    )
    validity.add_argument("--state-root", help="override SEW state root")
    validity.set_defaults(func=_gap_validate_task)
    gap_report = gap_sub.add_parser("report", help="write paired GAP JSON and markdown reports")
    gap_report.add_argument("run_root")
    gap_report.add_argument("--calibration", type=Path)
    gap_report.add_argument("--output-dir", type=Path)
    gap_report.add_argument("--price-table", type=Path)
    gap_report.add_argument("--seed", type=int, default=0)
    gap_report.set_defaults(func=_gap_report)
    gap_explain = gap_sub.add_parser("explain", help="explain GAP cell outcomes and usage")
    gap_explain.add_argument("run_root")
    gap_explain.add_argument("--calibration", type=Path)
    gap_explain.add_argument("--task", required=True)
    gap_explain.add_argument("--arm", required=True)
    gap_explain.add_argument("--format", choices=("markdown", "json"), default="markdown")
    gap_explain.set_defaults(func=_gap_explain)
    bakeoff = sub.add_parser("bakeoff", help="WSB web search bakeoff reports, bundles, and explain")
    bakeoff_sub = bakeoff.add_subparsers(dest="bakeoff_command", required=True)
    bakeoff_report = bakeoff_sub.add_parser(
        "report",
        help="write the comparative Markdown and JSON bakeoff report for a suite run",
    )
    bakeoff_report.add_argument("run_root", help="suite run root containing runner-state.json")
    bakeoff_report.add_argument(
        "--output-dir", help="report directory (default <run_root>/reports)"
    )
    bakeoff_report.add_argument("--price-table", help="override config/price-table.yaml")
    bakeoff_report.set_defaults(func=_bakeoff_report)
    bakeoff_grade = bakeoff_sub.add_parser("grade", help="grade captured live bakeoff cells")
    bakeoff_grade.add_argument("run_dir", help="run directory or suite run directory")
    bakeoff_grade.add_argument(
        "--judge-harness", choices=("claude-code", "codex"), default="claude-code"
    )
    bakeoff_grade.add_argument("--judge-model")
    bakeoff_grade.add_argument(
        "--second-judge-harness", "--second-judge", choices=("claude-code", "codex")
    )
    bakeoff_grade.add_argument("--second-judge-model")
    bakeoff_grade.add_argument(
        "--judge-max-tokens",
        type=int,
        default=DEFAULT_JUDGE_MAX_TOTAL_TOKENS,
        help="per-judge-call token budget, boot context and cache reads included",
    )
    bakeoff_grade.add_argument(
        "--judge-timeout-seconds", type=float, default=DEFAULT_JUDGE_TIMEOUT_SECONDS
    )
    bakeoff_grade.add_argument(
        "--wheelhouse",
        type=Path,
        help="verified offline wheel cache for GAP execution grading",
    )
    bakeoff_grade.add_argument("--regrade", action="store_true")
    bakeoff_grade.add_argument("--dry-run", action="store_true")
    bakeoff_grade.add_argument("--harness-auth", choices=("broker", "account"))
    bakeoff_grade.set_defaults(func=_bakeoff_grade)
    bakeoff_bundle = bakeoff_sub.add_parser(
        "bundle",
        help="assemble the reproducibility bundle and published headline report for a suite run",
    )
    bakeoff_bundle.add_argument("run_root", help="suite run root containing runner-state.json")
    bakeoff_bundle.add_argument(
        "--output-dir", help="publication directory (default <run_root>/publication)"
    )
    bakeoff_bundle.add_argument(
        "--include-raw-transcripts",
        action="store_true",
        help="operator opt-in: keep transcript text and harness stderr (credentials still stripped)",
    )
    bakeoff_bundle.add_argument("--price-table", help="override config/price-table.yaml")
    bakeoff_bundle.add_argument(
        "--no-archive", action="store_true", help="skip the shareable .tar.gz"
    )
    bakeoff_bundle.set_defaults(func=_bakeoff_bundle)
    bakeoff_verify = bakeoff_sub.add_parser(
        "verify",
        help="re-derive a bundle's aggregates from its own records and check its files",
    )
    bakeoff_verify.add_argument("bundle_dir", help="reproducibility bundle directory")
    bakeoff_verify.set_defaults(func=_bakeoff_verify)
    bakeoff_explain = bakeoff_sub.add_parser(
        "explain", help="show which dimension each arm failed on one task"
    )
    bakeoff_explain.add_argument(
        "run", help="suite run root, reproducibility bundle, or suite run id"
    )
    bakeoff_explain.add_argument("--task", required=True, help="task id")
    bakeoff_explain.add_argument("--price-table", help="override the run's price table")
    bakeoff_explain.set_defaults(func=_bakeoff_explain)
    domains = sub.add_parser("domains", help="DSB scorecard and hypothesis explanation")
    domain_sub = domains.add_subparsers(dest="domain_command", required=True)
    domain_report = domain_sub.add_parser("report", help="score a normalized domain run")
    domain_report.add_argument("run", help="JSON file with run_id and scored outcomes")
    domain_report.add_argument("--output-dir")
    domain_report.set_defaults(func=_domain_report)
    explain = domain_sub.add_parser("explain", help="render one hypothesis from a scorecard")
    explain.add_argument("report", help="generated report.json")
    explain.add_argument("--hypothesis", required=True)
    explain.set_defaults(func=_domain_explain)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (
        HostUnavailable,
        SchemaError,
        RunnerError,
        ReportError,
        DomainReportError,
        RetrievalLaneError,
        AgentLaneError,
    ) as exc:
        print(f"sew: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
