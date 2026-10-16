"""Read real enums and every status writer; independent of contract generation."""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sources():
    base = ROOT / "lib/python/sew"
    return {str(p.relative_to(base)): p.read_text() for p in base.rglob("*.py")}


def literal(text, symbol):
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == symbol for t in node.targets
        ):
            value = node.value
            return ast.literal_eval(value.args[0] if isinstance(value, ast.Call) else value)
    raise AssertionError("missing enum " + symbol)


def audit(src):
    schema = src["schema.py"]
    runs = set(literal(schema, "RUN_STATUSES"))
    calls = set(literal(schema, "PROVIDER_CALL_STATUSES"))
    outcomes = set(literal(schema, "EVALUATION_OUTCOMES"))
    mapping = literal(src["providers.py"], "SCHEMA_STATUS_BY_RESULT_STATUS")
    assert set(mapping.values()) == calls
    result = {}
    for id, names in [
        ("run", runs),
        ("provider-call", calls),
        ("provider-result", set(mapping)),
        ("evaluation", outcomes),
    ]:
        result["searchlight." + id] = [(n, "[*]", n) for n in sorted(names)] + [
            ("end." + n, n, "[*]") for n in sorted(names)
        ]
    # GAP reclassifies already-written records. Availability uses a closed
    # eligibility collection resolved from harness STATUS_* constants.
    constants = {
        n.targets[0].id: n.value.value
        for n in ast.parse(src["harness.py"]).body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and isinstance(n.value, ast.Constant)
    }
    node = next(
        n.value
        for n in ast.parse(src["harness.py"]).body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "PROVIDER_AVAILABILITY_STATUSES" for t in n.targets
        )
    )
    eligible = {
        constants[n.id] for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in constants
    }
    result["searchlight.run"] += [
        ("unavailable." + n, n, "provider_unavailable") for n in sorted(eligible)
    ]
    result["searchlight.run"] += [("contaminated." + n, n, "contaminated") for n in sorted(runs)]
    result["searchlight.run"] += [("budget", "succeeded", "budget_exhausted")]
    runner = src["runner.py"]
    assert "index.append(entry)" in runner and "index.remove(failed)" in runner
    assert "deferred[cell.key] = deferral" in runner
    assert "completed_keys" in runner and "_write_state" in runner
    result["searchlight.index"] = [
        ("create", "[*]", "unindexed"),
        ("persist", "unindexed", "indexed"),
        ("defer", "indexed", "deferred"),
        ("resume", "deferred", "indexed"),
        ("replay", "indexed", "indexed"),
    ]
    # Brief grading statuses are local result snapshots; nested agreement
    # statuses are separately inventoried derived observations.
    brief = src["gap/brief_grade.py"]
    values = {
        v.value
        for n in ast.walk(ast.parse(brief))
        if isinstance(n, ast.Dict)
        for k, v in zip(n.keys, n.values)
        if isinstance(k, ast.Constant) and k.value == "status" and isinstance(v, ast.Constant)
    } - {"not_measured", "measured"}
    result["searchlight.brief-grade"] = [(n, "[*]", n) for n in sorted(values)] + [
        ("end." + n, n, "[*]") for n in sorted(values)
    ]
    return result


def inventory(src=None):
    facts = {}
    for path, text in (src or sources()).items():
        selected = set()
        for n in ast.walk(ast.parse(text)):
            if isinstance(n, ast.keyword) and n.arg in {"status", "state", "outcome"}:
                selected.add(ast.unparse(n))
            if isinstance(n, (ast.Assign, ast.AnnAssign)):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                if any(
                    any(w in ast.unparse(t).lower() for w in ("status", "outcome")) for t in targets
                ):
                    selected.add(ast.unparse(n))
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if isinstance(k, ast.Constant) and k.value in {"status", "state", "outcome"}:
                        selected.add(str(k.value) + ": " + ast.unparse(v))
            if isinstance(n, ast.Compare) and any(
                w in ast.unparse(n.left).lower() for w in ("status", "outcome")
            ):
                selected.add(ast.unparse(n))
        if selected:
            facts[path] = sorted(selected)
    return facts


def definition():
    return json.loads((ROOT / "state-machines.json").read_text())


def verify(doc, src=None):
    src = src or sources()
    expected = audit(src)
    assert doc["sourceInventory"] == inventory(src)
    assert {o["id"] for o in doc["objects"]} == set(expected)
    for obj in doc["objects"]:
        edges = expected[obj["id"]]
        assert {
            (e["id"].removeprefix(obj["id"] + "."), e["from"], e["to"]) for e in obj["transitions"]
        } == set(edges)
        assert {s["name"] for s in obj["states"]} == {x for _, a, b in edges for x in (a, b)} - {
            "[*]"
        }
        for state in obj["states"]:
            if not state["terminal"]:
                assert state["exitOwner"] and state["overdueRecipient"]
                assert 0 < state["maxDurationSeconds"] < float("inf")
                assert state["enforcement"] in {"missing", "present"}
                if state["enforcement"] == "missing":
                    assert state["deadEndEvidence"]
