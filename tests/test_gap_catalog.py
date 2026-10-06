from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml

from sew.gap.catalog import FAMILIES, load_gap_tasks, validate_gap_catalog, validate_task
from sew.schema import SchemaError

GOLDEN = Path(__file__).parent / "fixtures" / "gap"
TASKS = json.loads((GOLDEN / "tasks.json").read_text())


@pytest.fixture
def catalog(tmp_path):
    base = tmp_path / "catalogs" / "gap"
    shutil.copytree(GOLDEN, base)
    return tmp_path, base


def write_catalog(root, tasks):
    (root / "catalogs/gap/tasks.yaml").write_text(
        yaml.safe_dump(
            dict(
                schema_version=1,
                catalog_id="gap-test",
                authoring_policy="Synthetic schema goldens.",
                tasks=tasks,
            )
        )
    )


@pytest.mark.parametrize("task", TASKS, ids=lambda t: t["family"])
def test_golden(task, catalog):
    root, base = catalog
    assert validate_task(task, base) == task
    write_catalog(root, [task])
    assert validate_gap_catalog(root) == [task["id"]]
    assert load_gap_tasks(root)[task["id"]] == task


def test_all_families_have_goldens():
    assert {task["family"] for task in TASKS} == FAMILIES


# Every required field at every nested level must independently refuse omission.
MISSING = [(index, (key,)) for index in (0, 5) for key in TASKS[index]]
MISSING += [
    (0, (parent, key))
    for parent in ("oracle", "verifier", "provenance", "budgets")
    for key in TASKS[0][parent]
]
MISSING += [(0, ("packages", 0, key)) for key in TASKS[0]["packages"][0]]


@pytest.mark.parametrize("index,path", MISSING)
def test_missing_fields(index, path, catalog):
    task = copy.deepcopy(TASKS[index])
    value = task
    for key in path[:-1]:
        value = value[key]
    del value[path[-1]]
    with pytest.raises(SchemaError):
        validate_task(task, catalog[1])


INVALID = [
    (("oracle", "sha256"), "0" * 64, "hash mismatch"),
    (("oracle", "excerpt"), "", "non-empty"),
    (("oracle", "excerpt"), "word " * 401, "400 words"),
    (("prompt",), "Use MODERN_CALL to finish.", "giveaway"),
    (("prompt",), "Install 2.0 and finish.", "giveaway"),
    (("giveaway_terms",), ["not_in_oracle"], "absent"),
    (("giveaway_terms",), [], "non-empty"),
    (("packages",), [], "pinned"),
    (("packages", 1, "role"), "dependency", "old and new"),
    (("packages", 1, "name"), "different", "pinned"),
    (("packages", 1, "version"), "1.0", "distinct"),
    (("packages", 1, "url"), "https://example.org/archive.zip", "wheel"),
    (("packages", 1, "sha256"), "bad", "sha256"),
    (("packages", 1, "role"), "future", "role"),
    (("packages", 1, "version"), "", "non-empty"),
    (("packages", 1, "role"), "old", "duplicate"),
    (("third_party_packages",), [], "pinned"),
    (("hidden",), "fixture", "disjoint"),
    (("hidden",), "fixture/subdir", "disjoint"),
    (("fixture",), "hidden", "disjoint"),
    (("fixture",), "missing", "does not exist"),
    (("hidden",), "missing", "does not exist"),
    (("hidden",), "../outside", "relative"),
    (("hidden",), "/tmp", "relative"),
    (("family",), "unknown", "family"),
    (("family",), "decision-brief", "task_type"),
    (("kind",), "control", "kind"),
    (("task_type",), "unknown", "task_type"),
    (("id",), "../bad", "identifier"),
    (("provenance", "event_date"), "2025-01-01", "postdate"),
    (("cutoff_after",), "bad", "ISO date"),
    (("oracle", "retrieved_at"), "bad", "ISO date"),
    (("oracle", "source_url"), "file:///tmp/source", "HTTPS"),
    (("oracle", "source_url"), "https://[broken", "HTTPS"),
    (("provenance", "source_urls"), [], "non-empty"),
    (("provenance", "source_urls"), ["https://user:secret@example.org"], "credentials"),
    (("verifier", "visible_commands"), [], "non-empty"),
    (("verifier", "hidden_commands"), [""], "non-empty"),
    (("verifier", "timeout_seconds"), 0, "positive"),
    (("budgets", "max_total_tokens"), True, "integer"),
    (("budgets", "max_wall_clock_seconds"), -1, "positive"),
    (("budgets", "max_provider_calls"), -1, "nonnegative"),
]


@pytest.mark.parametrize("path,value,message", INVALID)
def test_refusals(path, value, message, catalog):
    task = copy.deepcopy(TASKS[0])
    target = task
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    (catalog[1] / "fixture/subdir").mkdir()
    with pytest.raises(SchemaError, match=message):
        validate_task(task, catalog[1])


def test_control_dependencies(catalog):
    task = copy.deepcopy(TASKS[-1])
    task["third_party_packages"] = ["examplelib"]
    with pytest.raises(SchemaError, match="pinned"):
        validate_task(task, catalog[1])
    task["packages"] = [dict(TASKS[0]["packages"][0], role="dependency")]
    assert validate_task(task, catalog[1]) == task


@pytest.mark.parametrize("key,value", [("deliverable_schema", {}), ("rubric", "missing")])
def test_brief_refusals(key, value, catalog):
    task = copy.deepcopy(TASKS[5])
    task[key] = value
    with pytest.raises(SchemaError):
        validate_task(task, catalog[1])


def test_symlink_boundaries(catalog):
    root, base = catalog
    task = copy.deepcopy(TASKS[0])
    (base / "fixture/leak").symlink_to(base / "hidden", target_is_directory=True)
    with pytest.raises(SchemaError, match="symlink"):
        validate_task(task, base)
    (base / "fixture/leak").unlink()
    (base / "alias").symlink_to(base / "fixture", target_is_directory=True)
    task["hidden"] = "alias"
    with pytest.raises(SchemaError, match="disjoint"):
        validate_task(task, base)
    (base / "outside").symlink_to(root, target_is_directory=True)
    task["hidden"] = "outside"
    with pytest.raises(SchemaError, match="escapes"):
        validate_task(task, base)


def test_listing_never_reads_hidden(catalog, monkeypatch):
    root, base = catalog
    write_catalog(root, TASKS)
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert not path.resolve().is_relative_to(base / "hidden"), "hidden asset opened"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert validate_gap_catalog(root) == [task["id"] for task in TASKS]


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unknown", "version", "not-list", "bad-yaml", "not-mapping"]
)
def test_catalog_refusals(catalog, mutation):
    root, base = catalog
    write_catalog(root, TASKS)
    path = base / "tasks.yaml"
    data = yaml.safe_load(path.read_text())
    if mutation == "duplicate":
        data["tasks"].append(TASKS[0])
    if mutation == "unknown":
        data["extra"] = True
    if mutation == "version":
        data["schema_version"] = True
    if mutation == "not-list":
        data["tasks"] = {}
    path.write_text(
        "[" if mutation == "bad-yaml" else yaml.safe_dump([] if mutation == "not-mapping" else data)
    )
    with pytest.raises(SchemaError):
        load_gap_tasks(root)


def test_exact_excerpt_bytes_and_budget_zero(catalog):
    task = copy.deepcopy(TASKS[0])
    task["oracle"]["excerpt"] += "\n"
    with pytest.raises(SchemaError, match="hash mismatch"):
        validate_task(task, catalog[1])
    task["oracle"]["sha256"] = hashlib.sha256(task["oracle"]["excerpt"].encode()).hexdigest()
    task["budgets"]["max_provider_calls"] = 0
    assert validate_task(task, catalog[1]) == task


def test_shipped_catalog_loads():
    assert load_gap_tasks()


@pytest.mark.parametrize(
    "path,value",
    [
        (("oracle",), []),
        (("verifier",), None),
        (("provenance",), ""),
        (("budgets",), []),
        (("packages",), {}),
        (("packages", 0), "wheel"),
        (("task_type",), []),
        (("family",), {}),
        (("giveaway_terms",), "modern_call"),
    ],
)
def test_malformed_shapes(path, value, catalog):
    task = copy.deepcopy(TASKS[0])
    target = task
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(SchemaError):
        validate_task(task, catalog[1])


@pytest.mark.parametrize(
    "path", [(), ("oracle",), ("verifier",), ("provenance",), ("budgets",), ("packages", 0)]
)
def test_unknown_fields(path, catalog):
    task = copy.deepcopy(TASKS[0])
    target = task
    for key in path:
        target = target[key]
    target["typo"] = True
    with pytest.raises(SchemaError, match="unknown"):
        validate_task(task, catalog[1])


def test_control_brief(catalog):
    task = copy.deepcopy(TASKS[5])
    task.update(family="control", kind="control", giveaway_terms=[])
    assert validate_task(task, catalog[1]) == task


def test_giveaway_boundaries(catalog):
    task = copy.deepcopy(TASKS[0])
    task["prompt"] = "Write a modern_callback to process 12.0 records."
    assert validate_task(task, catalog[1]) == task
