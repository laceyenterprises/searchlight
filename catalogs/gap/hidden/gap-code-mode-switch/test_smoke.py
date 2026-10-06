import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import cli; from click.testing import CliRunner; assert CliRunner().invoke(cli,[]).output.strip()=='fast'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
