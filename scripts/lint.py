"""Dependency-free Python syntax lint for the workbench and its tests."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def lint(root=ROOT):
    files = sorted(path for directory in ('lib', 'tests', 'scripts')
                   for path in (root / directory).rglob('*.py'))
    for path in files:
        ast.parse(path.read_bytes(), filename=str(path))
    return len(files)


if __name__ == '__main__':
    print(f'Syntax lint passed: {lint()} Python files')
