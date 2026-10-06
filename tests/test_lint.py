import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('searchlight_lint', ROOT / 'scripts' / 'lint.py')
lint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lint)


def test_repository_has_no_mermaid_semicolons():
    assert lint.mermaid_problems() == []


def test_semicolon_in_sequence_diagram_is_reported(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'doc.md').write_text(
        '```mermaid\nsequenceDiagram\n  A->>B: labels; agreement\n```\n\n'
        '```mermaid\nflowchart LR\n  a-->b;\n```\n')
    (tmp_path / 'scripts' / 'site.py').write_text('X = {"g": """sequenceDiagram\n  A->>B: ok, fine\n  Note over A: x; y\n"""}\n')
    problems = lint.mermaid_problems(tmp_path)
    assert len(problems) == 2
    assert problems[0].startswith('doc.md:3:') and problems[1].startswith('scripts/site.py:3:')


@pytest.mark.parametrize('message', [
    'J2-->>A: independent labels; agreement recorded',
    'Note over A,J2: independent labels; agreement recorded',
])
def test_semicolon_on_triple_quote_closing_line_is_reported(tmp_path, message):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'site.py').write_text(
        'X = {"g": """sequenceDiagram\n  ' + message + '""",}\n'
        'unrelated = "allowed; outside the diagram"\n')

    problems = lint.mermaid_problems(tmp_path)

    assert len(problems) == 1
    assert problems[0].startswith('scripts/site.py:2:')
    assert message in problems[0]


def test_clean_triple_quote_closing_line_ends_diagram(tmp_path):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts' / 'site.py').write_text(
        'X = {"g": """sequenceDiagram\n  J2-->>A: independent labels, agreement recorded""",}\n'
        'unrelated = "allowed; outside the diagram"\n')

    assert lint.mermaid_problems(tmp_path) == []
