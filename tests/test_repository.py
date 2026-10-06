"""The standalone repository entry points stay usable after extraction."""
import ast
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_default_collection_excludes_verifier_corpus():
    import configparser
    config = configparser.ConfigParser()
    config.read(ROOT / 'pytest.ini')
    assert config['pytest']['testpaths'].split() == ['tests']


def test_ci_covers_all_supported_platforms_offline():
    workflow = yaml.safe_load((ROOT / '.github/workflows/standalone.yml').read_text())
    job = workflow['jobs']['standalone']
    assert job['strategy']['matrix'] == {
        'os': "${{ fromJSON((github.event_name == 'schedule' || "
              "github.event_name == 'workflow_dispatch') && "
              "'[\"ubuntu-latest\",\"macos-latest\"]' || '[\"ubuntu-latest\"]') }}",
        'python': ['3.11', '3.12', '3.13']}
    assert job['env']['SEW_MODE'] == 'standalone'
    assert any('scripts/public-clone-proof.sh' in step.get('run', '') for step in job['steps'])
    assert 'scripts/test-offline.sh' in (ROOT / 'scripts/public-clone-proof.sh').read_text()
    assert 'secrets.' not in (ROOT / '.github/workflows/standalone.yml').read_text()


def test_ci_concurrency_preserves_scheduled_and_manual_coverage():
    workflow = yaml.safe_load((ROOT / '.github/workflows/standalone.yml').read_text())
    # Different trigger types must not supersede each other's platform coverage.
    assert workflow['concurrency']['group'] == (
        'standalone-${{ github.event.pull_request.number || github.ref }}-'
        '${{ github.event_name }}')
    assert workflow['concurrency']['cancel-in-progress'] is True


def test_lint_rejects_broken_python(tmp_path):
    spec = importlib.util.spec_from_file_location('repository_lint', ROOT / 'scripts/lint.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'lib').mkdir()
    (tmp_path / 'lib/broken.py').write_text('def broken(\n')
    with pytest.raises(SyntaxError):
        module.lint(tmp_path)


def test_offline_runner_denies_external_access():
    runner = (ROOT / 'scripts/test-offline.sh').read_text()
    assert 'unshare --net' in runner
    assert 'block return out proto { tcp udp } all user root' in runner
    assert 'SEW_REQUIRE_SEATBELT=1' in runner
    assert 'SEW_REQUIRE_BUBBLEWRAP=1' in runner


@pytest.mark.skipif(__import__('os').environ.get('SEW_OFFLINE_TESTS') != '1',
                    reason='requires the OS-isolated CI runner')
def test_external_network_is_denied_in_child_process():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, '-c',
        'import socket\n'
        'try:\n'
        '    socket.create_connection(("1.1.1.1", 443), timeout=2)\n'
        'except OSError:\n'
        '    pass\n'
        'else:\n'
        '    raise AssertionError("external network allowed")\n'],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
