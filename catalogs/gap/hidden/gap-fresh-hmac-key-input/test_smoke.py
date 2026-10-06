import subprocess
import sys


def test_application_smoke():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import inspect\nimport runpy\nfrom pytest import MonkeyPatch\nnamespace = runpy.run_path(\'hidden/gap-fresh-hmac-key-input/test_hidden.py\')\nfor name, function in namespace.items():\n    if name.startswith("test_") and name not in {"test_offline_advisory", "test_delivered_security_pin"}:\n        with MonkeyPatch.context() as patches:\n            function(patches) if "monkeypatch" in inspect.signature(function).parameters else function()\n',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
