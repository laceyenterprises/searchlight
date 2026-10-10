"""Publication gates must reject damaged reports, without access to private evidence."""
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_reports', ROOT / 'scripts/check_reports.py')
reports = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reports)


def test_published_reports_pass():
    assert reports.check() == []


@pytest.fixture
def publication(tmp_path):
    shutil.copytree(ROOT / 'reports', tmp_path / 'reports')
    for name in ('gap', 'production'):
        destination = tmp_path / 'catalogs' / name
        destination.mkdir(parents=True)
        shutil.copy(ROOT / 'catalogs' / name / 'tasks.yaml', destination)
    # The only cross-report Markdown links target these documents/catalogs.
    for name in ('README.md', 'WORKBENCH.md', 'RUNBOOK-gap.md'):
        shutil.copy(ROOT / name, tmp_path)
    return tmp_path


def test_numeric_drift_rejected(publication):
    path = publication / 'reports' / reports.REPORTS[1] / 'REPORT.md'
    path.write_text(path.read_text().replace('94%', '99%'))
    assert any('differs from summary' in error for error in reports.check(publication))


def test_replication_numeric_drift_rejected(publication):
    name = '2026-10-06-search-gap-replication'
    path = publication / 'reports' / name / 'REPORT.md'
    text = path.read_text()
    assert '83%' in text
    path.write_text(text.replace('83%', '99%'))
    assert any(name + ': REPORT.md differs from summary' in error
               for error in reports.check(publication))


def test_render_check_rejects_line_ending_drift(publication):
    path = publication / 'reports' / reports.REPORTS[1] / 'REPORT.md'
    path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
    assert any('differs from summary' in error for error in reports.check(publication))


@pytest.mark.parametrize('identifier', [
    'op://' + 'vault/key', '/Users/' + 'someone/work', '/home/' + 'someone/work',
    '"/private/someone/work"', '`/Volumes/work`', r'C:\Users\someone\work',
    'https://github.com/example/' + 'agent-os/pull/1', 'LRQ-' + 'private-ticket',
    'run_id=' + 'private-run', 'worker.internal', 'wsb-full-20260929T0329Z',
    'file:///Users/someone/work', 'file:///home/someone/work',
    'file:///private/someone/work', 'file:///Volumes/work',
    'file://localhost/Users/someone/work', 'FILE:///users/someone/work',
])
def test_scrub_rejects_planted_identifiers(publication, identifier):
    path = publication / 'reports' / 'README.md'
    path.write_text(path.read_text() + '\n' + identifier)
    assert any('internal identifier' in error for error in reports.check(publication))


def test_broken_relative_link_rejected(publication):
    path = publication / 'reports' / 'README.md'
    path.write_text(path.read_text() + '\n[missing](missing.md)\n')
    assert any('broken link' in error for error in reports.check(publication))


@pytest.mark.parametrize('filename', ['REPORT.md', 'summary.json'])
def test_unresolved_report_template_is_rejected(publication, filename):
    path = publication / 'reports' / reports.REPORTS[-1] / filename
    content = path.read_text()
    expression = "{gap_rows[('claude-code', 'native')]['refused_calls']}"
    if filename == 'summary.json':
        summary = json.loads(content)
        summary['blocks'][0]['markdown'] += '\n' + expression
        path.write_text(json.dumps(summary))
    else:
        path.write_text(content + '\n' + expression)
    assert any('unresolved template expression' in error for error in reports.check(publication))


def test_catalog_drift_rejected(publication):
    path = publication / 'catalogs/gap/tasks.yaml'
    path.write_text(path.read_text() + '\n# catalog drift\n')
    assert any('catalog hash changed' in error for error in reports.check(publication))


def test_calibration_drift_rejected(publication):
    path = publication / 'reports' / reports.REPORTS[1] / 'calibration.json'
    data = json.loads(path.read_text())
    data['harnesses']['codex']['tasks'][0]['floor_passes'] = 1
    path.write_text(json.dumps(data))
    assert any('calibration does not match' in error for error in reports.check(publication))


@pytest.mark.parametrize('name', ['2026-10-03-search-gap-bench', '2026-10-06-search-gap-replication'])
@pytest.mark.parametrize('calibration', [
    {}, None, [], {'record_type': 'not-applicable'},
    {'harnesses': {}}, {'harnesses': None}, {'harnesses': []},
])
def test_gap_requires_calibration_independently_of_calibration_keys(publication, name, calibration):
    path = publication / 'reports' / name / 'calibration.json'
    path.write_text(json.dumps(calibration))
    assert reports.check(publication) == [f'{name}: calibration requires non-empty harness records']


@pytest.mark.parametrize('name', ['2026-10-03-search-gap-bench', '2026-10-06-search-gap-replication'])
@pytest.mark.parametrize('damage', ['missing-harness', 'empty-record', 'missing-tasks', 'empty-tasks', 'short-tasks'])
def test_gap_rejects_incomplete_calibration_records(publication, name, damage):
    path = publication / 'reports' / name / 'calibration.json'
    calibration = json.loads(path.read_text())
    harness = next(iter(calibration['harnesses']))
    if damage == 'missing-harness':
        del calibration['harnesses'][harness]
        expected = 'calibration harnesses do not match summary columns' if calibration['harnesses'] else \
            'calibration requires non-empty harness records'
    elif damage == 'empty-record':
        calibration['harnesses'][harness] = {}
        expected = 'calibration requires non-empty task records'
    else:
        record = calibration['harnesses'][harness]
        if damage == 'missing-tasks':
            del record['tasks']
        elif damage == 'empty-tasks':
            record['tasks'] = []
        else:
            record['tasks'].pop()
        expected = 'calibration task count does not match summary' if damage == 'short-tasks' else \
            'calibration requires non-empty task records'
    path.write_text(json.dumps(calibration))
    errors = reports.check(publication)
    assert len(errors) == 1 and errors[0].startswith(f'{name}: ') and expected in errors[0]


def _add_oss_harness(directory, *, summary_column=True):
    """A synthetic third calibrated harness on an OSS model, rendered as a publisher would."""
    calibration_path, summary_path = directory / 'calibration.json', directory / 'summary.json'
    calibration = json.loads(calibration_path.read_text())
    harness = json.loads(json.dumps(calibration['harnesses']['codex']))
    harness['model'] = 'litellm/glm-5.2'
    calibration['harnesses']['opencode'] = harness
    calibration_path.write_text(json.dumps(calibration))
    summary = json.loads(summary_path.read_text())
    if summary_column:
        table = next(b['table'] for b in summary['blocks'] if 'table' in b and b['table'][0][0] == 'Task')
        for row in table:
            row.append(row[-1] if row[0] != 'Task' else 'opencode')
    summary_path.write_text(json.dumps(summary))
    (directory / 'REPORT.md').write_text(reports.render(summary))


def test_three_harness_calibration_with_an_oss_model_passes(publication):
    _add_oss_harness(publication / 'reports' / reports.REPORTS[1])
    assert reports.check(publication) == []


def test_three_harness_calibration_checks_every_column(publication):
    directory = publication / 'reports' / reports.REPORTS[1]
    _add_oss_harness(directory)
    path = directory / 'calibration.json'
    data = json.loads(path.read_text())
    data['harnesses']['opencode']['tasks'][0]['ceiling_passes'] = 3
    path.write_text(json.dumps(data))
    errors = reports.check(publication)
    assert any('opencode/' in error and 'violates calibration thresholds' in error for error in errors)
    assert any('calibration does not match' in error for error in errors)


def test_calibrated_harness_without_a_summary_column_is_rejected(publication):
    _add_oss_harness(publication / 'reports' / reports.REPORTS[1], summary_column=False)
    errors = reports.check(publication)
    assert errors == [f'{reports.REPORTS[1]}: calibration harnesses do not match summary columns']


def test_every_calibrated_report_is_checked(publication):
    for name in reports.REPORTS:
        path = publication / 'reports' / name / 'calibration.json'
        data = json.loads(path.read_text())
        if 'harnesses' not in data:
            continue
        data['harnesses'][next(iter(data['harnesses']))]['tasks'][0]['floor_passes'] += 1
        path.write_text(json.dumps(data))
        assert any(error.startswith(f'{name}: ') for error in reports.check(publication, reports=(name,)))


def test_reproduction_suite_covers_wsb_matrix():
    import yaml
    from sew.schema import load_suite_manifest

    directory = ROOT / 'reports' / reports.REPORTS[0]
    suite = load_suite_manifest(directory)
    catalog = yaml.safe_load((ROOT / 'catalogs/production/tasks.yaml').read_text())
    assert set(suite['tasks']) == {task['id'] for task in catalog['tasks']}
    assert len(suite['providers']) == 8
    assert suite['repetitions'] == 3


def test_head_to_head_sets_match_their_manifest():
    import hashlib
    code = ROOT / 'reports' / '2026-09-26-search-api-head-to-head' / 'code'
    lines = (code / 'sets' / 'SHA256SUMS').read_text().splitlines()
    listed = {}
    for line in lines:
        digest, path = line.split('  ', 1)
        listed[path] = digest
        assert hashlib.sha256((code / path).read_bytes()).hexdigest() == digest, path
    shipped = {f'sets/{p.name}' for p in (code / 'sets').glob('*.json')} | {'stage_c/sets/SV.json'}
    assert set(listed) == shipped


def test_head_to_head_monitors_record_rebuilds_from_published_raw(tmp_path):
    import subprocess
    import sys
    source = ROOT / 'reports' / '2026-09-26-search-api-head-to-head'
    copy = tmp_path / 'report'
    shutil.copytree(source, copy)
    subprocess.run([sys.executable, 'monitors_harvest.py', 'export'], cwd=copy / 'code', check=True,
                   capture_output=True)
    published = sorted((source / 'data/stage-b/runs/SM').glob('*.json'))
    assert {p.name for p in published} >= {'summary.json', 'runs_m01.json', 'runs_m05.json'}
    for path in published:
        assert (copy / 'data/stage-b/runs/SM' / path.name).read_bytes() == path.read_bytes(), path.name


def test_gap_reproduction_restricts_calibration_to_all_briefs():
    import re
    import yaml
    path = ROOT / 'reports' / reports.REPORTS[1] / 'reproduce.md'
    selected = re.findall(r'--task (gap-[\w-]+)', path.read_text())
    catalog = yaml.safe_load((ROOT / 'catalogs/gap/tasks.yaml').read_text())
    assert set(selected) == {task['id'] for task in catalog['tasks'] if task['id'].startswith('gap-brief-')}
    assert len(selected) == 10


@pytest.mark.parametrize('digest', [None, '13f44a72'])
def test_missing_or_abbreviated_historical_hash_rejected(publication, digest):
    path = publication / 'reports' / reports.REPORTS[1] / 'summary.json'
    data = json.loads(path.read_text())
    data['catalog']['historical']['battery'] = digest
    path.write_text(json.dumps(data))
    assert any('incomplete historical catalog hash' in error for error in reports.check(publication))


def test_private_name_check_from_private_environment(publication, monkeypatch):
    name = 'example-private-operator'
    monkeypatch.setenv('SEW_REPORT_PRIVATE_NAMES', '\n unused-private-token \n' + name + '\n')
    path = publication / 'reports' / 'README.md'
    path.write_text(path.read_text() + '\n' + name.upper())
    errors = reports.check(publication)
    assert any('internal identifier' in error for error in errors)
    assert all(name not in error.lower() for error in errors)


@pytest.mark.parametrize('value', [None, ''])
def test_private_names_are_optional(publication, monkeypatch, value):
    if value is None:
        monkeypatch.delenv('SEW_REPORT_PRIVATE_NAMES', raising=False)
    else:
        monkeypatch.setenv('SEW_REPORT_PRIVATE_NAMES', value)
    path = publication / 'reports' / 'README.md'
    path.write_text(path.read_text() + '\nexample-private-operator\n')
    assert reports.check(publication) == []


def test_private_name_matches_whole_tokens(publication, monkeypatch):
    monkeypatch.setenv('SEW_REPORT_PRIVATE_NAMES', 'example-private-operator')
    path = publication / 'reports' / 'README.md'
    path.write_text(path.read_text() + '\nexample-private-operator-extra\n')
    assert reports.check(publication) == []


@pytest.mark.parametrize('damage', ['missing', 'render'])
def test_earlier_errors_do_not_skip_later_report(publication, damage):
    first = publication / 'reports' / reports.REPORTS[0] / 'REPORT.md'
    if damage == 'missing':
        first.unlink()
    else:
        first.write_text(first.read_text() + '\ndrift\n')
    second = publication / 'reports' / reports.REPORTS[1] / 'REPORT.md'
    second.write_text(second.read_text() + '\ndrift\n')
    errors = reports.check(publication)
    assert any(reports.REPORTS[0] in error for error in errors)
    assert any(reports.REPORTS[1] + ': REPORT.md differs' in error for error in errors)


def test_scrub_skips_binary_assets_and_finder_metadata(publication):
    for name in ('.DS_Store', 'figure.png'):
        (publication / 'reports' / name).write_bytes(b'\xff\xfe\x00')
    assert reports.check(publication) == []


@pytest.mark.parametrize('filename', [
    'REPORT.md', 'summary.json', 'calibration.json', 'methodology.md', 'reproduce.md', 'extra.yaml',
])
def test_invalid_utf8_is_a_gate_error(publication, filename):
    path = publication / 'reports' / reports.REPORTS[0] / filename
    path.write_bytes(b'\xff\xfe\x00')
    assert any('UTF-8' in error for error in reports.check(publication))


@pytest.mark.parametrize('filename', ['summary.json', 'calibration.json'])
@pytest.mark.parametrize('report_index', [0, 1])
def test_invalid_json_is_a_gate_error_and_validation_continues(publication, filename, report_index):
    path = publication / 'reports' / reports.REPORTS[report_index] / filename
    path.write_text('{"private": "example-private-value",}')
    second = publication / 'reports' / reports.REPORTS[1 - report_index] / 'REPORT.md'
    second.write_text(second.read_text() + '\ndrift\n')
    scrub = publication / 'reports' / 'README.md'
    scrub.write_text(scrub.read_text() + '\nfile:///Users/someone/work\n')
    errors = reports.check(publication)
    assert any(reports.REPORTS[report_index] + ': invalid publication JSON (line 1, column ' in error for error in errors)
    assert any(reports.REPORTS[1 - report_index] + ': REPORT.md differs' in error for error in errors)
    assert any('reports/README.md: internal identifier' in error for error in errors)
    assert all('example-private-value' not in error for error in errors)


@pytest.mark.parametrize('url', [
    'https://api.github.com/users/example', 'https://example.org/home/',
    'https://example.org/Users/example', 'https://example.org/private/data',
    'https://example.org/Volumes/data',
])
def test_public_url_paths_are_allowed(publication, url):
    path = publication / 'reports' / 'README.md'
    path.write_text(path.read_text() + f'\n[source]({url})\n')
    assert reports.check(publication) == []


@pytest.mark.parametrize('harness,col', [('claude-code', 2), ('codex', 3)])
@pytest.mark.parametrize('floor,ceiling,admitted', [(2, 5, True), (0, 2, True), (0, 5, False)])
def test_consistently_transcribed_invalid_admission_rejected(publication, harness, col, floor, ceiling, admitted):
    directory = publication / 'reports' / reports.REPORTS[1]
    path = directory / 'calibration.json'
    calibration = json.loads(path.read_text())
    record = calibration['harnesses'][harness]['tasks'][0]
    record.update(floor_passes=floor, ceiling_passes=ceiling, admitted=admitted)
    path.write_text(json.dumps(calibration))
    path = directory / 'summary.json'
    summary = json.loads(path.read_text())
    table = next(b['table'] for b in summary['blocks'] if 'table' in b and b['table'][0][0] == 'Task')
    table[2][col] = f'Floor: {floor}, Ceiling: {ceiling} — ' + ('admitted' if admitted else 'rejected')
    path.write_text(json.dumps(summary))
    (directory / 'REPORT.md').write_text(reports.render(summary))
    errors = reports.check(publication)
    assert any('admission violates calibration thresholds' in error for error in errors)
    assert not any('differs from summary' in error or 'calibration does not match' in error for error in errors)


def test_calibration_catalog_identity_must_match_battery(publication):
    path = publication / 'reports' / reports.REPORTS[1] / 'calibration.json'
    calibration = json.loads(path.read_text())
    calibration['catalog_sha256'] = '0' * 64
    path.write_text(json.dumps(calibration))
    assert any('calibration catalog hash differs' in error for error in reports.check(publication))
