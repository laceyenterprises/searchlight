import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import app; from typer.testing import CliRunner; result=CliRunner().invoke(app(),['--port','1234']); assert result.exit_code==0; assert result.output.strip()=='1234'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
