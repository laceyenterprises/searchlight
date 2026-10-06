import re

from typer.testing import CliRunner
from app import app


def _message(output):
    # Typer draws usage errors in a Rich panel sized to the runner's terminal,
    # so the diagnostic can wrap across box lines. Compare words, not layout.
    return " ".join(re.sub(r"[\u2500-\u257f]", " ", output).split()).lower()


def test_port_cli():
    r = CliRunner().invoke(app(), ["--port", "9312"])
    assert r.exit_code == 0, r.exception
    assert r.output == "9312\n"


def test_bad_ports():
    for value in ("0", "70000", "oops"):
        r = CliRunner().invoke(app(), ["--port", value])
        # A usage error is a handled rejection, not an uncaught conversion
        # exception from a foreign Click ParamType after Typer vendors Click.
        assert r.exit_code == 2, r.exception
        # Click reports a rejected parameter as "Invalid value for '--port'";
        # a parser's own exception text is not part of that diagnostic.
        assert "invalid value for '--port'" in _message(r.output)
