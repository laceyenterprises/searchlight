"""Portable tariff loader and transparent relay, vendored from MCP metering."""

from __future__ import annotations
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from .mcp_meter import CallMeter


def load_tariffs(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Read the checked-in SEW-shaped tariff registry; unreadable means unpriced."""
    try:
        import yaml

        data = yaml.safe_load((path or Path(__file__).with_name("tariffs.yaml")).read_text())
        providers = data.get("providers") if isinstance(data, Mapping) else None
        return (
            {
                str(server): {
                    str(tool): dict(rule)
                    for tool, rule in tools.items()
                    if isinstance(rule, Mapping)
                }
                for server, tools in providers.items()
                if isinstance(tools, Mapping)
            }
            if isinstance(providers, Mapping)
            else {}
        )
    except (OSError, ImportError, ValueError):
        return {}
    except Exception as exc:
        # PyYAML's YAMLError is optional and cannot be named without importing it.
        if exc.__class__.__module__.startswith("yaml"):
            return {}
        raise


def _pump(
    source: BinaryIO,
    sink: BinaryIO,
    observe: Callable[[bytes], None],
    *,
    before: bool,
    close_sink: bool = False,
    admit: Callable[[bytes], tuple[bytes | None, list[bytes]]] | None = None,
    reply_sink: BinaryIO | None = None,
    reply_lock: threading.Lock | None = None,
    sink_lock: threading.Lock | None = None,
) -> None:
    try:
        for line in iter(source.readline, b""):
            if admit is not None:
                line, replies = admit(line)
                for reply in replies:
                    if reply_sink is not None:
                        with reply_lock or threading.Lock():
                            reply_sink.write(reply)
                            reply_sink.flush()
                if line is None:
                    continue
            if before:
                try:
                    observe(line)
                except Exception:
                    pass
            try:
                if sink_lock:
                    with sink_lock:
                        sink.write(line)
                        sink.flush()
                else:
                    sink.write(line)
                    sink.flush()
            except (BrokenPipeError, OSError):
                break
            if not before:
                try:
                    observe(line)
                except Exception:
                    pass
    finally:
        if close_sink:
            try:
                sink.close()
            except OSError:
                pass


def serve(
    command: Sequence[str],
    meter: CallMeter,
    *,
    env: Mapping[str, str] | None = None,
    inherit_env: bool = True,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
) -> int:
    child = subprocess.Popen(
        list(command),
        env={**(os.environ if inherit_env else {}), **(env or {})},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    old_handlers = {}

    def forward(signum: int, _frame: Any) -> None:
        try:
            child.send_signal(signum)
        except OSError:
            pass

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, forward)
        except (ValueError, OSError):
            pass
    upstream = threading.Thread(
        target=_pump,
        args=(
            stdin if stdin is not None else sys.stdin.buffer,
            child.stdin,
            (lambda _line: None)
            if meter.policy or meter.policy_loader or meter.admission_verifier
            else meter.client_line,
        ),
        kwargs={
            "before": True,
            "close_sink": True,
            "admit": meter.admit_line
            if meter.policy or meter.policy_loader or meter.admission_verifier
            else None,
            "reply_sink": stdout if stdout is not None else sys.stdout.buffer,
            "reply_lock": (output_lock := threading.Lock()),
        },
        daemon=True,
    )
    upstream.start()
    try:
        # Serialize server replies and local denials onto the same transport.
        _pump(
            child.stdout,
            stdout if stdout is not None else sys.stdout.buffer,
            meter.server_line,
            before=False,
            sink_lock=output_lock,
        )
        code = child.wait()
        meter.flush()
        return 128 - code if code < 0 else code
    finally:
        for signum, handler in old_handlers.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
