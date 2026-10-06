import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import click; from app import option; p=option(); ctx=click.Context(click.Command('s')); assert type(p.process_value(ctx,p.flag_value)) is str",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
