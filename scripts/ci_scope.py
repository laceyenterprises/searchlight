"""Conservative, dependency-free selection for the existing Standalone suite."""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ALWAYS_FULL = {
    "pyproject.toml",
    "setup.py",
    "MANIFEST.in",
    "pytest.ini",
    "tests/conftest.py",
    "scripts/ci_scope.py",
    "scripts/test-offline.sh",
    "scripts/public-clone-proof.sh",
}
DATA_ROOTS = {"tasks", "fixtures", "catalogs", "config", "modules"}
DATA_NAMES = {
    "state-machines.json",
    "post-merge-verify.json",
    "reproduce.md",
    "suite.yaml",
}


def data_path(path):
    p = PurePosixPath(path)
    return (
        p.parts[0] in DATA_ROOTS
        or p.name in DATA_NAMES
        or path.startswith("docs/architecture/")
        or (path.startswith("reports/") and bool({"code", "data"} & set(p.parts[1:-1])))
    )


def content_path(path):
    p = PurePosixPath(path)
    if data_path(path):
        return False
    return (
        p.parts[0] in {"site", "docs", "changes"}
        or (len(p.parts) == 1 and p.suffix == ".md")
        or (p.parts[0] == "reports" and p.suffix not in {".py", ".sh", ".js", ".ts"})
        or (p.parts[0] == "design" and p.suffix not in {".py", ".sh", ".js", ".ts"})
    )


def documentation_only(paths):
    return bool(paths) and all(content_path(path) for path in paths)


def path_literal(node):
    """Recognize both ROOT / 'scripts/file.py' and ROOT / 'scripts' / 'file.py'."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = path_literal(node.left), path_literal(node.right)
        if right:
            return f"{left}/{right}" if left else right
    return ""


def dependency_map(root, ambiguous=None):
    files = sorted(
        {
            p.relative_to(root).as_posix()
            for directory in ("lib/python/sew", "scripts", "tests", "design")
            for p in (root / directory).rglob("*.py")
        }
    )
    modules, path_names, claims = {}, {}, {}
    for path in files:
        name = (
            path.removeprefix("lib/python/")
            .removeprefix("scripts/")
            .removeprefix("tests/")
            .removeprefix("design/glyphs/")
            .removeprefix("design/")
        )
        name = name.removesuffix(".py").replace("/", ".")
        name = name.removesuffix(".__init__")
        path_names[path] = name
        claims.setdefault(name, []).append(path)
    for name, claimants in claims.items():
        if len(claimants) == 1:
            modules[name] = claimants[0]
        elif ambiguous is not None:
            ambiguous.update((path, name) for path in claimants)
    for path in files:
        qualified = path.removesuffix(".py").replace("/", ".")
        modules[qualified.removesuffix(".__init__")] = path
    edges, references = {}, {}
    for path in files:
        tree = ast.parse((root / path).read_bytes(), filename=path)
        names, refs = set(), set()
        module = path_names[path]
        package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
        for node in ast.walk(tree):
            literal = path_literal(node)
            if literal:
                refs.add(literal)
                if literal.startswith("sew."):
                    names.add(literal)  # subprocess python -m module names.
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                prefix = node.module or ""
                if node.level:
                    parts = package.split(".")
                    prefix = ".".join(
                        parts[: len(parts) - node.level + 1]
                        + ([prefix] if prefix else [])
                    )
                names.add(prefix)
                names.update(
                    f"{prefix}.{alias.name}" if prefix else alias.name
                    for alias in node.names
                )
        dependencies = {ref for ref in refs if ref in files}
        for name in names:
            # Python executes package initializers when importing a child.
            parts = name.split(".")
            for size in range(1, len(parts) + 1):
                target = modules.get(".".join(parts[:size]))
                if target:
                    dependencies.add(target)
        edges[path], references[path] = dependencies, refs
    closures = {}
    for path in files:
        seen, pending = set(), [path]
        while pending:
            item = pending.pop()
            if item not in seen:
                seen.add(item)
                pending.extend(edges[item] - seen)
        closures[path] = seen
    return closures, references


def select(paths, root=ROOT):
    """Return mode, per-test causes, reason and fixture-battery decision."""

    closures = None

    def battery_needed(graph):
        inputs = graph.get("scripts/offline-fixture-battery.py", set())
        return any(
            p in inputs
            or PurePosixPath(p).parts[0] in {"catalogs", "fixtures", "config", "tasks"}
            or p in {"pyproject.toml", "setup.py", "MANIFEST.in"}
            for p in paths
        )

    def full(reason):
        try:
            graph = closures if closures is not None else dependency_map(root)[0]
            battery = battery_needed(graph) if paths else True
        except (SyntaxError, OSError, UnicodeError, ValueError):
            battery = True
        return {"mode": "full", "tests": {}, "reason": reason, "battery": battery}

    if not paths:
        return full("empty or unavailable diff")
    for path in paths:
        if path in ALWAYS_FULL or path.startswith((".github/", "bin/")):
            return full(f"shared CI or packaging input: {path}")
    if documentation_only(paths):
        return {
            "mode": "content-only",
            "tests": {},
            "reason": "publication content only",
            "battery": False,
        }
    ambiguous = {}
    try:
        closures, references = dependency_map(root, ambiguous)
    except (SyntaxError, OSError, UnicodeError, ValueError) as error:
        return full(f"dependency map unavailable: {type(error).__name__}")
    for path in paths:
        if path in ambiguous:
            return full(f"ambiguous module name: {ambiguous[path]}")
    tests = sorted(
        p
        for p in closures
        if PurePosixPath(p).name.startswith("test_") and p.startswith("tests/")
    )
    data_owners = {}
    for test in tests:
        refs = {
            ref.rstrip("/")
            for source in closures[test]
            for ref in references[source]
            if ref
            and not ref.startswith((".", "/"))
            and ("/" in ref or ref in DATA_ROOTS or ref in DATA_NAMES)
        }
        for ref in refs:
            data_owners.setdefault(ref, set()).add(test)
    selected, unreached = {}, []
    for path in paths:
        if content_path(path):
            continue
        if (
            path.startswith("tests/")
            and not PurePosixPath(path).name.startswith("test_")
            and path in closures.get("tests/conftest.py", set())
        ):
            return full(f"shared fixture helper: {path}")
        affected = [test for test in tests if path in closures[test]]
        if data_path(path) or (
            path.startswith(("lib/python/sew/", "scripts/", "tests/"))
            and not path.endswith(".py")
        ):
            # Literal directory references cover computed descendants too. If no
            # source gives evidence of ownership, retain the full-suite backstop.
            parts = PurePosixPath(path).parts
            affected = sorted(
                set().union(
                    *(
                        data_owners.get("/".join(parts[:size]), set())
                        for size in range(1, len(parts) + 1)
                    )
                )
            )
        if not affected:
            if path in closures and path.endswith(".py"):
                unreached.append(f"no test reaches {path}")
                continue
            return full(f"unmapped path: {path}")
        for test in affected:
            selected.setdefault(test, []).append(path)
    return {
        "mode": "selected",
        "tests": dict(sorted(selected.items())),
        "reason": "; ".join(["static dependency closure", *unreached]),
        "battery": battery_needed(closures),
    }


def scope(event, base, head, root=ROOT):
    if event == "push":
        return {"mode": "smoke", "tests": {}, "reason": "main push", "battery": False}
    if event != "pull_request":
        return {
            "mode": "full",
            "tests": {},
            "reason": "scheduled/manual backstop",
            "battery": True,
        }
    try:
        output = subprocess.check_output(
            ["git", "diff", "--no-renames", "--name-only", "-z", base, head, "--"],
            cwd=root,
        )
        paths = output.decode("utf-8").rstrip("\0").split("\0") if output else []
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        paths = []
    return select(paths, root)


def full_suite(event, base, head):
    return scope(event, base, head)["mode"] == "full"


if __name__ == "__main__":
    decision = scope(
        os.environ["CI_EVENT"], os.environ["CI_BASE"], os.environ["CI_HEAD"]
    )
    lines = [f"CI tests: {decision['mode']} — {decision['reason']}"]
    lines += [
        f"- {test}: {', '.join(causes)}" for test, causes in decision["tests"].items()
    ]
    lines.append(f"Offline fixture battery: {decision['battery']}")
    print("\n".join(lines))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n")
    # JSON is read as an argv array by the workflow, never evaluated as shell.
    import json

    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        output.write(
            f"run_suite={str(decision['mode'] == 'full' or bool(decision['tests'])).lower()}\n"
        )
        output.write(f"test_files={json.dumps(list(decision['tests']))}\n")
        output.write(f"battery={int(decision['battery'])}\n")
