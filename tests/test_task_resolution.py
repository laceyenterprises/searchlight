"""WSBLIVE-01 production task resolution and provenance."""

from __future__ import annotations

import pytest

from conftest import MODULE_ROOT
from sew.harness import HarnessRunConfig
from sew.runner import BudgetTracker, LiveCellExecutor, MatrixCell, OperatorBudgets
from sew.schema import SchemaError, load_suite_manifest, validate_run_record
from sew.task_resolution import resolve_task
from test_schema import VALID_RUN


def test_resolution_and_contract() -> None:
    legacy = resolve_task("current-fact-lookup-v1")
    assert legacy.source == "legacy"
    production = resolve_task("list-build-python313-pep594-removals")
    assert production.source == "production"
    assert production.budgets["max_bytes"] > 0
    assert "Python 3.13 completed" in production.prompt
    assert '"removed_modules"' in production.prompt
    assert '"evidence_urls"' in production.prompt
    assert "evidence_urls" in production.prompt
    assert not any(
        word in production.prompt.lower() for word in ("brave", "tavily", "codex", "claude")
    )
    with pytest.raises(SchemaError, match="unknown task id"):
        resolve_task("missing-wsb-task")


def test_production_result_reservation_blocks_cell_before_suite_cap() -> None:
    production = resolve_task("list-build-python313-pep594-removals")
    budgets = production.budgets
    tracker = BudgetTracker(
        OperatorBudgets(
            max_provider_calls=budgets["max_provider_calls"],
            max_provider_result_chars=budgets["max_bytes"] - 1,
            max_total_tokens=budgets["max_total_tokens"],
            max_wall_clock_seconds=budgets["wall_clock_seconds"],
        )
    )
    assert tracker.can_start(production.task) == "provider_result_character_budget_exhausted"


def test_trial_suite_resolves() -> None:
    suite = load_suite_manifest(MODULE_ROOT / "catalogs" / "wsb-trial")
    assert set(suite["providers"]) == {"native", "brave", "tavily", "no-search"}
    assert all(resolve_task(task_id).source == "production" for task_id in suite["tasks"])


def test_live_cell_config_uses_catalog_budgets() -> None:
    cell = MatrixCell(
        suite_id="wsb-trial",
        suite_version="1",
        task_id="list-build-python313-pep594-removals",
        provider_id="native",
        harness_id="codex",
        model_profile="default",
        repetition=1,
        run_id="trial-cell",
        required_operation="search",
        applicable=True,
    )
    config = LiveCellExecutor()._harness_config(cell, budgets=None)
    assert isinstance(config, HarnessRunConfig)
    executor = LiveCellExecutor()
    from sew.runner import CellBudgets

    config = executor._harness_config(
        cell, budgets=CellBudgets.from_task(executor._task(cell.task_id))
    )
    assert (
        config.timeout_seconds,
        config.max_provider_calls,
        config.max_total_tokens,
    ) == (
        900,
        40,
        300000,
    )
    assert config.task_source == "production"
    assert "removed_modules" in config.prompt_text


def test_run_source_schema_backward_compatible() -> None:
    run = dict(VALID_RUN)
    assert validate_run_record(run)["task_source"] == "legacy"
    run["task_source"] = "production"
    assert validate_run_record(run)["task_source"] == "production"
    run["task_source"] = "unknown"
    with pytest.raises(SchemaError, match="task_source"):
        validate_run_record(run)


def test_open_deliverable_schema_without_required_resolves(tmp_path, monkeypatch) -> None:
    """JSON Schema makes `required` optional; an open schema must not crash resolution."""

    import sew.task_resolution as task_resolution

    entry = {
        "id": "open-schema-task",
        "task_class": "synthesis",
        "prompt": "Summarize the evidence.",
        "evidence_field": "sources",
        "deliverable_schema": {"type": "object"},
        "budgets": {
            "max_provider_calls": 5,
            "max_wall_clock_seconds": 60,
            "max_total_tokens": 1000,
        },
    }
    catalog = {
        "tasks": [entry],
        "budget_ceilings": {
            "max_provider_calls": 40,
            "max_wall_clock_seconds": 900,
            "max_total_tokens": 300000,
        },
    }
    monkeypatch.setattr(
        task_resolution,
        "load_production_catalog",
        lambda root=None: {"catalog": catalog},
    )
    resolved = resolve_task("open-schema-task", tmp_path)
    assert resolved.source == "production"
    assert '"required": []' in resolved.prompt
    assert '"properties": {}' in resolved.prompt


def test_malformed_production_budget_raises_schema_error(tmp_path, monkeypatch) -> None:
    import sew.task_resolution as task_resolution

    catalog = {
        "tasks": [
            {
                "id": "broken-budget",
                "task_class": "synthesis",
                "prompt": "Summarize the evidence.",
                "evidence_field": "sources",
                "deliverable_schema": {"type": "object"},
                "budgets": {"max_wall_clock_seconds": 60, "max_total_tokens": 1000},
            }
        ],
        "budget_ceilings": {
            "max_provider_calls": 40,
            "max_wall_clock_seconds": 900,
            "max_total_tokens": 300000,
        },
    }
    monkeypatch.setattr(
        task_resolution,
        "load_production_catalog",
        lambda root=None: {"catalog": catalog},
    )
    with pytest.raises(SchemaError, match="task broken-budget.budgets.max_provider_calls"):
        resolve_task("broken-budget", tmp_path)
