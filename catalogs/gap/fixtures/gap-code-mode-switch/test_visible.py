from click.testing import CliRunner
from app import cli


def test_explicit_flags():
    assert CliRunner().invoke(cli, ["--safe"]).output == "safe\n"
    assert CliRunner().invoke(cli, ["--fast"]).output == "fast\n"
