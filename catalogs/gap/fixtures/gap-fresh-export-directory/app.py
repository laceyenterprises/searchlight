from pathlib import Path
from click.testing import CliRunner


def export(text):
    with CliRunner().isolated_filesystem():
        path = Path("result.txt")
        path.write_text(text, encoding="utf-8")
        return path.read_text(encoding="utf-8")
