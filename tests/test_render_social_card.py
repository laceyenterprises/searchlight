import importlib.util
import signal
import subprocess
from pathlib import Path
from unittest.mock import Mock, call

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('render_social_card', ROOT / 'scripts' / 'render_social_card.py')
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


@pytest.fixture
def chrome_process(monkeypatch):
    def start(screenshot, returncode=None):
        proc = Mock(pid=12345, returncode=returncode)
        proc.poll.return_value = returncode

        def popen(argv, **kwargs):
            if screenshot is not None:
                shot = next(arg.split('=', 1)[1] for arg in argv if arg.startswith('--screenshot='))
                Path(shot).write_bytes(screenshot)
            return proc

        monkeypatch.setattr(renderer.subprocess, 'Popen', popen)
        ticks = iter([0.0, 1.0, 121.0])
        monkeypatch.setattr(renderer.time, 'monotonic', lambda: next(ticks))
        monkeypatch.setattr(renderer.time, 'sleep', Mock())
        monkeypatch.setattr(renderer.os, 'killpg', Mock())
        return proc

    return start


@pytest.mark.parametrize('screenshot', [None, b'partial PNG'])
@pytest.mark.parametrize('returncode', [0, 1, -9])
def test_exited_chrome_fails_immediately_without_complete_screenshot(chrome_process, screenshot, returncode):
    chrome_process(screenshot, returncode)
    with pytest.raises(SystemExit, match=rf'Chrome exited \({returncode}\) without a complete screenshot'):
        renderer.render(ROOT / 'site' / 'social-card.html', 'chrome')
    renderer.time.sleep.assert_not_called()
    renderer.os.killpg.assert_not_called()


@pytest.mark.parametrize('returncode', [None, 0])
def test_complete_screenshot_is_returned(chrome_process, returncode):
    screenshot = b'PNG data' + renderer.IEND
    proc = chrome_process(screenshot, returncode)
    assert renderer.render(ROOT / 'site' / 'social-card.html', 'chrome') == screenshot
    renderer.time.sleep.assert_not_called()
    if returncode is None:
        renderer.os.killpg.assert_called_once_with(proc.pid, signal.SIGTERM)
        proc.wait.assert_called_once_with(timeout=10)
    else:
        renderer.os.killpg.assert_not_called()


@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('failed_signal', [signal.SIGTERM, signal.SIGKILL])
@pytest.mark.parametrize('error', [ProcessLookupError, PermissionError])
def test_cleanup_races_preserve_render_result(chrome_process, complete, failed_signal, error):
    screenshot = b'PNG data' + renderer.IEND if complete else b'partial PNG'
    proc = chrome_process(screenshot)
    if failed_signal == signal.SIGKILL:
        proc.wait.side_effect = subprocess.TimeoutExpired('chrome', 10)

    def killpg(pid, sig):
        if sig == failed_signal:
            raise error('process group unavailable')

    renderer.os.killpg.side_effect = killpg
    if complete:
        assert renderer.render(ROOT / 'site' / 'social-card.html', 'chrome') == screenshot
    else:
        with pytest.raises(SystemExit, match='no screenshot after 120 s'):
            renderer.render(ROOT / 'site' / 'social-card.html', 'chrome')
    expected = [call(proc.pid, signal.SIGTERM)]
    if failed_signal == signal.SIGKILL:
        expected.append(call(proc.pid, signal.SIGKILL))
    assert renderer.os.killpg.call_args_list == expected
    proc.wait.assert_called_once_with(timeout=10)
