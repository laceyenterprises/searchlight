from click.testing import CliRunner
from app import cli


def test_default_mode():
    r = CliRunner().invoke(cli, [])
    assert r.exit_code == 0
    assert r.output == "fast\n"
