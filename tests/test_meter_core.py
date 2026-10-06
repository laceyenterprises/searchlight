from __future__ import annotations

import io
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from sew.mcp_meter import CallMeter
from sew.meter_core import serve


@pytest.mark.parametrize("background", [False, True])
def test_serve_preserves_exit_status_and_handlers(tmp_path, monkeypatch, background):
    meter = CallMeter(provider_id="brave", run_id="relay", call_dir=tmp_path, tariffs={})
    flush = Mock(wraps=meter.flush)
    monkeypatch.setattr(meter, "flush", flush)
    handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    }
    payload = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
    output = io.BytesIO()

    def relay():
        return serve(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.exit(23)",
            ],
            meter,
            stdin=io.BytesIO(payload),
            stdout=output,
        )

    if background:
        with ThreadPoolExecutor(max_workers=1) as executor:
            code = executor.submit(relay).result(timeout=10)
    else:
        code = relay()

    assert code == 23
    assert output.getvalue() == payload
    flush.assert_called_once_with()
    assert {signum: signal.getsignal(signum) for signum in handlers} == handlers
