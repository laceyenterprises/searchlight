"""Resolve live tasks from legacy manifests or the WSB production catalog."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .catalog import module_root
from .production_catalog import (
    _require_positive_int,
    _require_text,
    load_production_catalog,
)
from .schema import SchemaError, load_task_manifest


@dataclass(frozen=True)
class ResolvedTask:
    task: dict[str, Any]
    prompt: str
    budgets: dict[str, int]
    source: str


def resolve_task(task_id: str, root: Path | None = None) -> ResolvedTask:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", task_id):
        raise SchemaError(f"invalid task id: {task_id!r}")
    base = module_root() if root is None else root
    task_dir = base / "tasks" / task_id
    if task_dir.is_dir():
        task = load_task_manifest(task_dir)
        return ResolvedTask(
            task,
            (task_dir / "prompt.md").read_text(encoding="utf-8"),
            task["budgets"],
            "legacy",
        )

    catalog = load_production_catalog(base).get("catalog")
    if not isinstance(catalog, Mapping):
        raise SchemaError("production task catalog must be a mapping")
    tasks = catalog.get("tasks")
    if not isinstance(tasks, list):
        raise SchemaError("production task catalog.tasks must be a list")
    for entry in tasks:
        if not isinstance(entry, Mapping):
            raise SchemaError("production task catalog.tasks[] must be a mapping")
        if entry.get("id") != task_id:
            continue
        schema = entry.get("deliverable_schema")
        if not isinstance(schema, Mapping):
            raise SchemaError(f"task {task_id}.deliverable_schema must be a mapping")
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise SchemaError(f"task {task_id}.deliverable_schema has invalid fields")
        contract = {
            "type": "object",
            "properties": properties,
            "required": required,
        }
        evidence_field = _require_text(
            entry.get("evidence_field"), f"task {task_id}.evidence_field"
        )
        prompt = (
            _require_text(entry.get("prompt"), f"task {task_id}.prompt").strip()
            + "\n\nReturn one JSON object matching this deliverable contract. "
            + f"The evidence/citation field is {evidence_field!r}.\n"
            + json.dumps(contract, indent=2)
        )
        ceilings = catalog.get("budget_ceilings")
        task_budgets = entry.get("budgets")
        if not isinstance(ceilings, Mapping) or not isinstance(task_budgets, Mapping):
            raise SchemaError(f"task {task_id} requires catalog and task budget mappings")
        budget_keys = (
            "max_provider_calls",
            "max_wall_clock_seconds",
            "max_total_tokens",
        )
        bounded = {
            key: min(
                _require_positive_int(task_budgets.get(key), f"task {task_id}.budgets.{key}"),
                _require_positive_int(ceilings.get(key), f"budget_ceilings.{key}"),
            )
            for key in budget_keys
        }
        budgets = {
            "max_provider_calls": bounded["max_provider_calls"],
            "wall_clock_seconds": bounded["max_wall_clock_seconds"],
            "max_total_tokens": bounded["max_total_tokens"],
            # Reserve a bounded result size per cell. A zero reservation let
            # every production cell start immediately before the suite cap.
            "max_bytes": min(100_000, bounded["max_total_tokens"] * 4),
        }
        # Suite scheduling consumes the legacy task shape; production grading
        # still reads the original catalog entry by id.
        task = {
            "task_id": task_id,
            "task_class": _require_text(entry.get("task_class"), f"task {task_id}.task_class"),
            "fixture_mode_allowed": False,
            "budgets": budgets,
        }
        return ResolvedTask(task, prompt, budgets, "production")
    raise SchemaError(f"unknown task id: {task_id}")
