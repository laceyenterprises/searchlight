"""Exercise real CLI gates with disposable repositories and installed metadata."""
import email.message
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/publication-gates.py'
spec = importlib.util.spec_from_file_location('publication_gates', SCRIPT)
gates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    # Disposable repositories have no GitHub refs unless a test plants them.
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.org')
    (tmp_path / 'README.md').write_text('Portable public tree\n')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'Clean snapshot')
    return tmp_path


def run_gate(gate, root):
    return subprocess.run([sys.executable, str(SCRIPT), gate, '--root', str(root)],
                          capture_output=True, text=True)


@pytest.mark.parametrize('path', ['README.md', 'reports/report.json', '.github/workflows/check.yml'])
def test_hardcode_gate_clean_and_planted(repo, path):
    assert run_gate('hardcodes', repo).returncode == 0
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('/Users/' + 'operator/agent-os-hq/state')
    git(repo, 'add', '.')
    assert run_gate('hardcodes', repo).returncode == 1
    target.write_text('Use the configured state root')
    git(repo, 'add', '.')
    assert run_gate('hardcodes', repo).returncode == 0


@pytest.mark.parametrize('location', ['tree', 'deleted', 'branch', 'message', 'tag'])
def test_secret_gate_clean_and_planted(repo, location):
    assert run_gate('secrets', repo).returncode == 0
    token = 'gh' + 'p_' + 'Ab19Cd28Ef37Gh46Ij55Kl64Mn73Op82Qr91St00'
    if location == 'tag':
        git(repo, 'tag', '-a', 'bad', '-m', token)
    elif location == 'message':
        git(repo, 'commit', '--allow-empty', '-m', token)
    else:
        if location == 'branch':
            git(repo, 'checkout', '-b', 'other')
        (repo / 'key.txt').write_text(token)
        git(repo, 'add', '.')
        if location != 'tree':
            git(repo, 'commit', '-m', 'Planted fixture')
        if location == 'deleted':
            git(repo, 'rm', 'key.txt')
            git(repo, 'commit', '-m', 'Delete fixture')
        if location == 'branch':
            git(repo, 'checkout', '-')
    result = run_gate('secrets', repo)
    assert result.returncode == 1, result.stdout
    assert token not in result.stdout


def test_synthetic_scrub_values_do_not_hide_real_keys():
    synthetic = ('sk-' + 'ant-api03-' + 'SECRET' * 4).encode()
    assert gates.findings(synthetic, gates.SECRETS) == []
    assert gates.findings(synthetic + b'\nghp_' + b'Ab19' * 10, gates.SECRETS)


@pytest.mark.parametrize('expression,allowed', [
    ('MIT', True), ('Apache-2.0 AND BSD-3-Clause', True),
    ('MIT OR GPL-3.0-only', False), ('GPL-3.0-only', False),
    ('LicenseRef-Private', False), ('', False), ('MIT!!!', False),
    ('MIT BSD-3-Clause', False), ('MIT OR', False), ('(MIT', False),
    ('(MIT OR Apache-2.0) AND BSD-3-Clause', True),
])
def test_licence_policy(expression, allowed):
    info = email.message.Message()
    if expression:
        info['License-Expression'] = expression
    assert gates.licence_allowed(info) is allowed


def test_licence_gate_rejects_transitive_dependency(monkeypatch):
    class Distribution:
        version = '1.0'
        def __init__(self, licence, requires=()):
            self.metadata = email.message.Message()
            self.metadata['License-Expression'] = licence
            self.requires = requires
    distributions = {'searchlight': Distribution('Apache-2.0', ['child']),
                     'child': Distribution('MIT', ['grandchild']),
                     'grandchild': Distribution('BSD-3-Clause')}
    monkeypatch.setattr(gates.metadata, 'distribution', distributions.__getitem__)
    assert gates.licence_audit() == []
    distributions['grandchild'] = Distribution('GPL-3.0-only')
    assert gates.licence_audit() == ['grandchild==1.0: missing or non-permissive licence']


def test_public_clone_proof_propagates_failure(tmp_path):
    # Stub external commands to exercise the shell's failure propagation quickly.
    source = Path(__file__).resolve().parents[1]
    script = source / 'scripts/public-clone-proof.sh'
    commands = tmp_path / 'commands'
    commands.mkdir()
    fake = commands / 'git'
    fake.write_text('#!/bin/sh\nexit 23\n')
    fake.chmod(0o755)
    import os
    result = subprocess.run(['bash', str(script)], env={**os.environ, 'PATH': f'{commands}:/usr/bin:/bin'},
                            capture_output=True)
    assert result.returncode == 23


@pytest.mark.parametrize('phase', ['clean', 'install', 'cache', 'doctor', 'suite', 'battery'])
def test_public_clone_runs_every_phase_and_fails_closed(tmp_path, phase):
    import os
    commands = tmp_path / 'commands'
    commands.mkdir()
    git_stub = commands / 'git'
    git_stub.write_text('''#!/bin/bash
if [[ "$*" == *clone* ]]; then
 mkdir -p "${@: -1}/scripts"
 printf '#!/bin/sh\nexec python3 -m pytest -q\n' > "${@: -1}/scripts/test-offline.sh"
 chmod +x "${@: -1}/scripts/test-offline.sh"
fi
if [[ "$*" == *rev-parse* ]]; then echo deadbeef; fi
''')
    python_stub = commands / 'python3'
    python_stub.write_text(f'''#!/bin/bash
venv="${{@: -1}}"
mkdir -p "$venv/bin"
cat > "$venv/bin/python3" <<'COMMAND'
#!/bin/bash
[[ "${{SWX_PLANTED_SECRET+x}}" == "" ]] || exit 29
[[ "${{SEW_MODE:-}}" == standalone ]] || exit 29
[[ "${{SEW_TEST_INSTALLED:-}}" == 1 ]] || exit 29
[[ "${{HOME:-}}" == */home ]] || exit 29
if [[ "$*" == *pip* ]]; then exit {17 if phase == 'install' else 0}; fi
if [[ "$*" == *offline-fixture-battery* ]]; then exit {20 if phase == 'battery' else 0}; fi
if [[ "$*" == *prepare-gap-code-wheelhouse* ]]; then exit {21 if phase == 'cache' else 0}; fi
if [[ "$*" == *pytest* ]]; then exit {19 if phase == 'suite' else 0}; fi
exit 25
COMMAND
cat > "$venv/bin/sew" <<'COMMAND'
#!/bin/bash
if [[ "$1" == doctor ]]; then
 echo 'mode         standalone'
 exit {18 if phase == 'doctor' else 0}
fi
exit {20 if phase == 'battery' else 0}
COMMAND
chmod +x "$venv/bin/python3" "$venv/bin/sew"
''')
    git_stub.chmod(0o755)
    python_stub.chmod(0o755)
    result = subprocess.run(['bash', str(SCRIPT.with_name('public-clone-proof.sh'))],
                            env={**os.environ, 'PATH': f'{commands}:/usr/bin:/bin',
                                 'SWX_PLANTED_SECRET': 'synthetic-parent-secret'},
                            capture_output=True, text=True)
    assert result.returncode == {'clean': 0, 'install': 17, 'doctor': 18,
                                 'suite': 19, 'battery': 20, 'cache': 21}[phase], result.stderr
    assert ('public-clone proof passed' in result.stdout) is (phase == 'clean')


def test_licence_cli_clean_and_planted_metadata(tmp_path):
    import os
    for name, requirements in [('searchlight', 'Requires-Dist: child\n'), ('child', '')]:
        dist = tmp_path / f'{name}-1.0.dist-info'
        dist.mkdir()
        (dist / 'METADATA').write_text(f'Metadata-Version: 2.4\nName: {name}\nVersion: 1.0\nLicense-Expression: MIT\n{requirements}')
    def check():
        return subprocess.run([sys.executable, str(SCRIPT), 'licences'],
                              env={**os.environ, 'PYTHONPATH': str(tmp_path)},
                              capture_output=True, text=True)
    assert check().returncode == 0
    child = tmp_path / 'child-1.0.dist-info/METADATA'
    child.write_text(child.read_text().replace('MIT', 'GPL-3.0-only'))
    assert check().returncode == 1


def test_history_gate_refuses_shallow_clone(repo, tmp_path):
    clone = tmp_path / 'shallow'
    subprocess.run(['git', 'clone', '--depth=1', repo.as_uri(), str(clone)], check=True,
                   capture_output=True)
    result = run_gate('secrets', clone)
    assert result.returncode == 2
    assert 'full clone' in result.stdout


@pytest.mark.parametrize('namespace', ['refs/pull', 'refs/remotes/pull'])
def test_history_gate_requires_pull_refs_in_ci(repo, monkeypatch, namespace):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    result = run_gate('secrets', repo)
    assert result.returncode == 2
    assert 'requires fetched pull-request refs' in result.stdout
    git(repo, 'update-ref', f'{namespace}/1/head', 'HEAD')
    assert run_gate('secrets', repo).returncode == 0


def test_publication_fetch_scans_closed_deleted_pull_request(repo, tmp_path, monkeypatch):
    token = 'gh' + 'p_' + 'Ab19' * 10
    git(repo, 'checkout', '-b', 'abandoned')
    (repo / 'key.txt').write_text(token)
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Abandoned PR')
    git(repo, 'update-ref', 'refs/pull/1/head', 'HEAD')
    git(repo, 'checkout', '-')
    git(repo, 'branch', '-D', 'abandoned')
    remote = tmp_path / 'remote.git'
    git(repo, 'clone', '--bare', str(repo), str(remote))
    git(repo, 'push', str(remote), 'refs/pull/1/head')
    clone = tmp_path / 'checkout'
    git(repo, 'clone', str(remote), str(clone))
    assert not git(clone, 'for-each-ref', 'refs/pull', 'refs/remotes/pull').stdout
    workflow = yaml.safe_load((SCRIPT.parents[1] / '.github/workflows/standalone.yml').read_text())
    steps = workflow['jobs']['standalone']['steps']
    fetch = next(step for step in steps if step.get('name') == 'Fetch pull-request history')
    # Execute the actual CI fetch against a local server with a hidden PR ref.
    subprocess.run(['bash', '-euo', 'pipefail', '-c', fetch['run']], cwd=clone,
                   env={**os.environ, 'GITHUB_TOKEN': 'synthetic-test-token'},
                   check=True, capture_output=True)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    result = run_gate('secrets', clone)
    assert result.returncode == 1, result.stdout
    assert token not in result.stdout
    assert 'github-token' in result.stdout


@pytest.mark.parametrize('content', [
    lambda value: 'API_KEY = "' + value + '"',
    lambda value: '{"api_key": "' + value + '"}',
    lambda value: 'password: "' + value + '"',
    lambda value: 'SEW_BRAVE_API_KEY=' + value,
    lambda value: 'api_key: ' + value,
])
def test_secret_assignment_formats(content):
    value = 'Ab19Cd28Ef37Gh46Ij55Kl64Mn73Op82Qr91St00'
    assert gates.findings(content(value).encode(), gates.SECRETS) == ['credential-assignment']


@pytest.mark.skipif(os.environ.get('SEW_TEST_PUBLICATION_BATTERY') != '1',
                    reason='Full battery runs once in the dedicated public clone proof')
def test_offline_battery_completes_the_committed_matrix():
    spec = importlib.util.spec_from_file_location('offline_battery', SCRIPT.with_name('offline-fixture-battery.py'))
    battery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(battery)
    result = battery.run_battery()
    assert result['completed_cells'] == result['total_cells'] == 168
    assert result['status_counts']['succeeded'] > 0


@pytest.mark.parametrize('failure', ['incomplete', 'failed-cell'])
def test_offline_battery_rejects_incomplete_result(monkeypatch, failure):
    spec = importlib.util.spec_from_file_location('offline_battery', SCRIPT.with_name('offline-fixture-battery.py'))
    battery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(battery)
    result = {'remaining_cells': 158, 'completed_cells': 10, 'total_cells': 168,
              'status_counts': {'succeeded': 9}, 'stopped_reason': 'provider_call_budget_exhausted'}
    if failure == 'failed-cell':
        result.update(remaining_cells=0, completed_cells=168, stopped_reason=None,
                      status_counts={'succeeded': 155, 'not_applicable': 12, 'failed': 1})
    monkeypatch.setattr(battery.SuiteRunner, 'run', lambda *a, **k: result)
    with pytest.raises(RuntimeError, match='did not complete'):
        battery.run_battery()


@pytest.mark.parametrize('category,value', [
    ('account-path', '/home/' + 'runner/agent-os/data'),
    ('private-repository', 'https://github.com/' + 'example-org/' + 'agent-os'),
    ('private-vault', 'op://' + 'test-vault/test-item/' + 'credential'),
    ('private-network', '100.' + '64.12.34'),
])
def test_each_portable_denylist_rule(category, value):
    assert category in gates.findings(value.encode(), gates.HARDCODES)


@pytest.mark.parametrize('category,value', [
    ('private-key', '-----BEGIN ' + 'PRIVATE KEY-----'),
    ('private-key', '-----BEGIN ' + 'RSA ' + 'PRIVATE KEY-----'),
    ('private-key', '-----BEGIN ' + 'EC ' + 'PRIVATE KEY-----'),
    ('private-key', '-----BEGIN ' + 'OPENSSH ' + 'PRIVATE KEY-----'),
    ('github-token', 'gh' + 'p_' + 'Ab19' * 10),
    ('github-token', 'github_' + 'pat_' + 'Ab19_' * 16),
    ('provider-key', 'sk-' + 'Ab19' * 12),
    ('provider-key', 'sk-' + 'proj-' + 'Ab19' * 12),
    ('provider-key', 'sk-' + 'ant-' + 'Ab19' * 12),
    ('aws-key', 'AK' + 'IA' + 'AB12' * 4),
    ('aws-key', 'AS' + 'IA' + 'AB12' * 4),
])
def test_each_secret_signature_rule(category, value):
    assert category in gates.findings(value.encode(), gates.SECRETS)


@pytest.mark.parametrize('template', ['"{}"', '({})', '<{}>', '{}\nMore text'])
def test_private_repository_url_terminators(template):
    url = 'https://github.com/' + 'example-org/' + 'agent-os'
    assert gates.findings(template.format(url).encode(), gates.HARDCODES) == ['private-repository']
    assert gates.findings((url + '-public').encode(), gates.HARDCODES) == []


@pytest.mark.parametrize('target_exists', [False, True])
def test_tree_audit_reads_symlink_target_blob(repo, tmp_path, target_exists):
    target = tmp_path / 'host' / 'agent-os-hq' / 'state'
    if target_exists:
        target.parent.mkdir(parents=True)
        target.write_text('Clean host file')
    (repo / 'host-link').symlink_to(target)
    git(repo, 'add', 'host-link')
    # The temp path is portable; a separate rule proves the link text is read.
    assert gates.tree_audit(repo, {'host-link': r'/host/agent-os-hq/state'}) == ['host-link: host-link']


def test_tree_audit_cannot_be_masked_by_unstaged_edits(repo):
    token = 'gh' + 'p_' + 'Ab19' * 10
    (repo / 'key.txt').write_text(token)
    git(repo, 'add', 'key.txt')
    (repo / 'key.txt').write_text('Clean working copy')
    result = run_gate('secrets', repo)
    assert result.returncode == 1
    assert token not in result.stdout


@pytest.mark.parametrize('prefix', ['tvly-', 'pplx-', 'fc-', 'sk_live_', 'xoxb-', 'xoxa-', 'xoxp-'])
def test_provider_fixture_headers(prefix):
    assert 'provider-key' in gates.findings(('Authorization: ' + prefix + 'Ab19' * 10).encode(), gates.SECRETS)


@pytest.mark.parametrize('header', ['ENCRYPTED PRIVATE KEY', 'PGP PRIVATE KEY BLOCK'])
def test_encrypted_private_key_headers(header):
    assert 'private-key' in gates.findings(('-----BEGIN ' + header + '-----').encode(), gates.SECRETS)


@pytest.mark.parametrize('encoding', ['utf-16', 'gzip', 'zip', 'tar'])
def test_encoded_and_archived_history_secret(repo, encoding):
    import gzip
    import io
    import tarfile
    import zipfile
    token = ('gh' + 'p_' + 'Ab19' * 10)
    if encoding == 'utf-16':
        data = token.encode('utf-16')
    elif encoding == 'gzip':
        data = gzip.compress(token.encode())
    else:
        stream = io.BytesIO()
        if encoding == 'zip':
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('fixture.txt', token)
        else:
            with tarfile.open(fileobj=stream, mode='w') as archive:
                member = tarfile.TarInfo('fixture.txt')
                member.size = len(token)
                archive.addfile(member, io.BytesIO(token.encode()))
        data = stream.getvalue()
    (repo / 'fixture.bin').write_bytes(data)
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Archived fixture')
    git(repo, 'rm', 'fixture.bin')
    git(repo, 'commit', '-m', 'Deleted archived fixture')
    result = run_gate('secrets', repo)
    assert result.returncode == 1
    assert token not in result.stdout
    assert 'github-token' in result.stdout


def test_archive_limits_and_malformed_content_fail_closed(monkeypatch):
    import gzip
    monkeypatch.setattr(gates, 'MAX_SCAN_BYTES', 1024)
    assert gates.findings(gzip.compress(b'A' * 2000), gates.SECRETS) == ['archive-limit']
    assert gates.findings(b'PK\x03\x04malformed', gates.SECRETS) == ['archive-error']


def test_historical_hardcodes_report_visibility_decision(repo):
    import json
    (repo / 'old.txt').write_text('/Users/' + 'operator/agent-os-hq/state')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'Historical topology')
    git(repo, 'rm', 'old.txt')
    git(repo, 'commit', '-m', 'Remove topology')
    result = run_gate('hardcodes', repo)
    assert result.returncode == 0
    report = json.loads((repo / 'publication-hardcode-history.json').read_text())
    assert report['requires_visibility_decision']
    assert any('account-path' in value for value in report['findings'])


@pytest.mark.parametrize('marker', ['sys_platform == "darwin"', 'python_version < "3.12"'])
def test_licence_markers_cover_supported_target_platforms(monkeypatch, marker):
    class Distribution:
        version = '1.0'
        def __init__(self, licence, requires=()):
            self.metadata = email.message.Message()
            self.metadata['License-Expression'] = licence
            self.requires = requires
    distributions = {'searchlight': Distribution('Apache-2.0', ['child; ' + marker]),
                     'child': Distribution('GPL-3.0-only')}
    monkeypatch.setattr(gates.metadata, 'distribution', distributions.__getitem__)
    assert gates.licence_audit() == ['child==1.0: missing or non-permissive licence']


def test_fetch_token_is_command_scoped_environment_not_argv(tmp_path):
    import json
    commands = tmp_path / 'commands'
    commands.mkdir()
    output = tmp_path / 'arguments.json'
    fake = commands / 'git'
    fake.write_text('#!' + sys.executable + '\nimport json,os,sys\n'
                    'json.dump({"argv":sys.argv,"config":os.environ.get("GIT_CONFIG_VALUE_0")},'
                    'open(os.environ["FETCH_CAPTURE"],"w"))\n')
    fake.chmod(0o755)
    workflow = yaml.safe_load((SCRIPT.parents[1] / '.github/workflows/standalone.yml').read_text())
    fetch = next(step for step in workflow['jobs']['standalone']['steps']
                 if step.get('name') == 'Fetch pull-request history')
    subprocess.run(['bash', '-euo', 'pipefail', '-c', fetch['run']], check=True,
                   env={**os.environ, 'PATH': f'{commands}:/usr/bin:/bin',
                        'FETCH_CAPTURE': str(output), 'GITHUB_TOKEN': 'synthetic-token'})
    captured = json.loads(output.read_text())
    assert captured['config'].startswith('AUTHORIZATION: basic ')
    assert not any('AUTHORIZATION' in arg or 'synthetic-token' in arg for arg in captured['argv'])


def test_synthetic_bearer_examples_do_not_exempt_adjacent_real_token():
    synthetic = 'Bearer ' + 'abcdefghijklmnopqrstuvwxyz0123456789'
    assert gates.findings(synthetic.encode(), gates.SECRETS) == []
    assert gates.findings((synthetic + '\nBearer ' + 'Zx19' * 10).encode(), gates.SECRETS) == ['bearer-token']
    assert 'provider-key' in gates.findings(('AI' + 'za' + 'Z' * 35).encode(), gates.SECRETS)
