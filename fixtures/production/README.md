# WSB production-catalog fixtures

Offline fixtures for `catalogs/production/`. Nothing here is a benchmark
result: no arm produced any of it and no live run wrote it. These are
hand-authored payloads whose only job is to hold the catalog and the
deterministic scorer honest without a network.

## `model-answers/`

One deliverable per deterministically scored task — sixteen files — each of
which must score `passed: true`.

The property being tested is **satisfiability**. A task whose ground truth no
deliverable can satisfy makes every arm fail for a reason that has nothing to
do with the arm, and it looks exactly like a hard task in the report. A
mistyped regex, a host allowlist that excludes its own required host, or a
`min_members` floor above the number of members the brief asks for all produce
that failure, and none of them is visible by reading the YAML. So every task
ships a worked answer and
`test_production_catalog.py::test_every_deterministic_task_is_satisfiable`
checks it.

Where a task's instances are supplied per run — the ordering regression, the
npm dependent ranking — the model answer uses `example-` placeholders and
illustrative figures. Those files demonstrate the required evidence *shape*,
not a fact.

## `golden/`

Four pinned scoring records, two tasks × two deliverables each:

| deliverable | scores |
|---|---|
| `python313-removals.complete` | `passed: true`, 3/3 fields |
| `python313-removals.superset` | `passed: false` — carries three modules the brief excludes, restates the count to match, and loses a citation |
| `postgres-guc.honest` | `passed: true`, 4/4 fields |
| `postgres-guc.fabricated` | `passed: false` — declines in the status field while asserting a default value in the prose beside it |

Each `*.deliverable.json` has a `*.score.json` holding the exact record
`score_deliverable` must produce. Two deliverables per task rather than one is
deliberate: a golden that only pins a pass proves the scorer can say yes, not
that it can say no, and a scorer that always says yes would pin just as
cleanly.

`postgres-guc.fabricated` is the one worth reading. `answer_status` is
correctly `no_reliable_answer` and the explanation beside it still asserts a
default value and a range for a parameter that does not exist. Scoring the
decline field alone would pass it, which is why the `decline` check scans the
whole deliverable.

## Regenerating

The score records are generated output. After an intended change to the
scorer or to a task's ground truth, re-pin them:

```bash
cd modules/search-evaluation-workbench
PYTHONPATH=lib/python python3 - <<'PY'
import json, pathlib
from sew.production_catalog import load_production_catalog
from sew.production_scoring import score_deliverable

TASK_BY_STEM = {
    "python313-removals": "list-build-python313-pep594-removals",
    "postgres-guc": "unanswerable-nonexistent-postgres-guc",
}
tasks = {t["id"]: t for t in load_production_catalog()["catalog"]["tasks"]}
base = pathlib.Path("fixtures/production/golden")
for path in sorted(base.glob("*.deliverable.json")):
    stem = path.name.removesuffix(".deliverable.json")
    task = tasks[TASK_BY_STEM[stem.split(".")[0]]]
    record = score_deliverable(task, json.loads(path.read_text()))
    (base / f"{stem}.score.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
PY
```

Re-pinning is not a way to make a red test green. If a golden changes, the
diff is the behaviour change, and it belongs in the PR description.
