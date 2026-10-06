"""Dependency-free lint for the workbench: Python syntax and Mermaid sequence diagrams."""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def lint(root=ROOT):
    files = sorted(path for directory in ('lib', 'tests', 'scripts')
                   for path in (root / directory).rglob('*.py'))
    for path in files:
        ast.parse(path.read_bytes(), filename=str(path))
    return len(files)


def mermaid_problems(root=ROOT):
    """Mermaid reads ';' in a sequence diagram as a statement separator, so a
    message or note containing one fails to parse (GitHub shows "Unable to
    render rich display"). Scan Markdown and the site generator's diagrams."""
    paths = sorted(p for p in root.rglob('*.md') if '.git' not in p.parts)
    paths += sorted((root / 'scripts').glob('*.py'))
    problems = []
    for path in paths:
        inside = False
        for number, line in enumerate(path.read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            stripped = line.strip()
            if not inside:
                inside = stripped.endswith('sequenceDiagram') or stripped.endswith('"""sequenceDiagram')
                continue
            if ';' in stripped:
                problems.append(f'{path.relative_to(root)}:{number}: ";" in a Mermaid sequence diagram ends the '
                                f'statement early; use a comma: {stripped}')
            if stripped.startswith('```') or '"""' in stripped:
                inside = False
    return problems


if __name__ == '__main__':
    count = lint()
    problems = mermaid_problems()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        sys.exit(1)
    print(f'Syntax lint passed: {count} Python files; Mermaid sequence diagrams parse-safe')
