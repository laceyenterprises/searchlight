import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app import customer_id; from types import SimpleNamespace; service=SimpleNamespace(retrieve=lambda identifier, options=None: SimpleNamespace(id=identifier)); client=SimpleNamespace(v1=SimpleNamespace(customers=service)); assert customer_id(client,'cus_smoke')=='cus_smoke'",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
