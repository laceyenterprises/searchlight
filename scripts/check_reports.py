"""Offline integrity gates for published reports; no private source checkout needed."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
INSIGHT_SCHEMA = 'sew-insight-summary'


def _schema(directory):
    """The summary schema a report directory declares: 'insight', or 'transcription' for everything else.

    A summary that cannot be read is a transcription, so the gates below still check and report it."""
    try:
        data = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 'transcription'
    return 'insight' if isinstance(data, dict) and data.get('schema') == INSIGHT_SCHEMA else 'transcription'


def discover(root=ROOT, kind='transcription'):
    """Every published report directory under reports/ of one kind, in date order (directory names start with it).

    Publishing a new report directory, or running `sew insight publish`, is all it takes for the gates and the
    site to pick it up: there is no list to edit."""
    base = Path(root) / 'reports'
    if not base.is_dir():
        return ()
    return tuple(sorted(d.name for d in base.iterdir()
                        if d.is_dir() and not d.name.startswith('.') and _schema(d) == kind))


REPORTS = discover()
# Private infrastructure and credentials are never valid report content.
# File URIs need an explicit branch: the path boundary below permits web URLs.
FORBIDDEN = re.compile(
    r'op://' r'|(?<![\w/:])(?:/Users/|/home/|/private/|/Volumes/|[A-Z]:\\Users\\)'
    r'|file://[^\s)\"]*/(?:Users|home|private|Volumes)/'
    r'|\bHQ_ROOT\b|https?://[^\s)\"]*/agent-os(?:/|(?=[\s)\"]|$))'
    r'|\b(?:LRQ[-_:]|lrq[-_:])\S+|\b(?:run_id|run-id|suite-run-id)\s*[:=]'
    # Placeholder hosts in quoted third-party docs (artifacts.company.internal) are not ours.
    r'|(?<!\bcompany)(?<!\bexample)\.internal\b'
    r'|\b(?:wsb|gap)[-_][\w-]*\d{8}T\d{4,6}Z\b', re.I
)


# Scan publication text only; images and Finder metadata are not UTF-8 text.
TEXT_SUFFIXES = frozenset({'.md', '.json', '.yaml', '.yml'})
# Unevaluated report-source expressions must never reach published prose.
UNRESOLVED_TEMPLATE = re.compile(r'\{\s*\w+\s*\[[^{}]+\]\s*\}')


def has_private_name(content, private_names):
    return any(word.casefold() in private_names for word in re.findall(r"[\w-]+", content))


def render(summary):
    """Preserve recorded display precision, table order and narrative caveats."""
    pieces = []
    for block in summary['blocks']:
        if 'markdown' in block:
            pieces.append(block['markdown'])
        else:
            pieces.append('\n'.join('| ' + ' | '.join(row) + ' |' for row in block['table']))
    return '\n'.join(pieces).strip() + '\n\n' + (
        'Read the [summary data](summary.json), [calibration record](calibration.json), '
        '[methodology](methodology.md) and [reproduction steps](reproduce.md).\n'
    )


def check_calibration(name, data, calibration):
    """A calibrated GAP report's admission table, one column per calibrated harness."""
    errors = []
    if (not isinstance(calibration, dict)
            or not isinstance(calibration.get('harnesses'), dict)
            or not calibration['harnesses']):
        return [f'{name}: calibration requires non-empty harness records']
    if any(key not in calibration for key in ('catalog_sha256', 'floor_max_passes', 'ceiling_min_passes')):
        return [f'{name}: calibration metadata is incomplete']
    if calibration['catalog_sha256'] != data['catalog']['historical']['battery']:
        errors.append(f'{name}: calibration catalog hash differs from historical battery')
    table = next(b['table'] for b in data['blocks'] if 'table' in b and b['table'][0][0] == 'Task')
    if table[0][2:] != list(calibration['harnesses']):
        return errors + [f'{name}: calibration harnesses do not match summary columns']
    for col, (harness, record_set) in enumerate(calibration['harnesses'].items(), start=2):
        tasks = record_set.get('tasks') if isinstance(record_set, dict) else None
        if not isinstance(tasks, list) or not tasks:
            errors.append(f'{name}: {harness}: calibration requires non-empty task records')
            continue
        if len(tasks) != len(table[2:]):
            errors.append(f'{name}: {harness}: calibration task count does not match summary')
            continue
        for record, row in zip(tasks, table[2:], strict=True):
            admitted = (
                record['floor_passes'] <= calibration['floor_max_passes']
                and record['ceiling_passes'] >= calibration['ceiling_min_passes']
            )
            if record['admitted'] is not admitted:
                errors.append(f'{name}: {harness}/{record["task"]}: admission violates calibration thresholds')
            expected = f"Floor: {record['floor_passes']}, Ceiling: {record['ceiling_passes']} — " + ('admitted' if record['admitted'] else 'rejected')
            if row[col] != expected or row[0] != record['task'] or row[1] != record['family']:
                errors.append(f'{name}: calibration does not match summary')
    return errors


def check_insight(root, name):
    """An insight study: its REPORT.md and dashboard.html must be the renderings of its summary.json."""
    if str(ROOT / 'lib' / 'python') not in sys.path:
        sys.path.insert(0, str(ROOT / 'lib' / 'python'))
    from sew.insight.dashboard import render_dashboard
    from sew.insight.report import render as render_insight

    directory = Path(root) / 'reports' / name
    errors = [f'{name}: missing {f}' for f in ('REPORT.md', 'summary.json', 'dashboard.html', 'methodology.md',
                                               'reproduce.md') if not (directory / f).is_file()]
    if errors:
        return errors
    try:
        summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
        report = (directory / 'REPORT.md').read_bytes().decode('utf-8')
        dashboard = (directory / 'dashboard.html').read_bytes().decode('utf-8')
    except (UnicodeDecodeError, ValueError) as exc:
        return [f'{name}: unreadable publication data ({type(exc).__name__})']
    if report != render_insight(summary):
        errors.append(f'{name}: REPORT.md differs from summary.json rendering')
    if dashboard != render_dashboard(summary):
        errors.append(f'{name}: dashboard.html differs from summary.json rendering; run sew insight publish again')
    return errors


def check(root=ROOT, reports=None, studies=None):
    root = Path(root)
    reports = discover(root) if reports is None else reports
    studies = discover(root, 'insight') if studies is None else studies
    errors = []
    for name in studies:
        errors.extend(check_insight(root, name))
    # Optional private input, supplied outside Git; never print its values.
    private_names = frozenset(
        name.strip().casefold()
        for name in os.environ.get('SEW_REPORT_PRIVATE_NAMES', '').splitlines()
        if name.strip()
    )
    for name in reports:
        directory = root / 'reports' / name
        missing = []
        for filename in ('REPORT.md', 'summary.json', 'calibration.json', 'methodology.md', 'reproduce.md'):
            if not (directory / filename).is_file():
                missing.append(f'{name}: missing {filename}')
        errors.extend(missing)
        if missing:
            continue
        try:
            data = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
            report = (directory / 'REPORT.md').read_bytes().decode('utf-8')
            calibration = json.loads((directory / 'calibration.json').read_text(encoding='utf-8'))
        except UnicodeDecodeError as exc:
            errors.append(f'{name}: publication data is not UTF-8 ({exc.reason})')
            continue
        except json.JSONDecodeError as exc:
            errors.append(f'{name}: invalid publication JSON (line {exc.lineno}, column {exc.colno})')
            continue
        if report != render(data):
            errors.append(f'{name}: REPORT.md differs from summary.json transcription')
        catalog = data['catalog']
        actual = hashlib.sha256((root / catalog['path']).read_bytes()).hexdigest()
        if actual != catalog['shipped']:
            errors.append(f'{name}: shipped catalog hash changed; update reproduction metadata')
        for digest in catalog['historical'].values():
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                errors.append(f'{name}: incomplete historical catalog hash')
        if data['bench'] == 'GAP':
            errors.extend(check_calibration(name, data, calibration))
    for path in (root / 'reports').rglob('*'):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            errors.append(f'{path.relative_to(root)}: publication text is not UTF-8')
            continue
        if FORBIDDEN.search(content) or has_private_name(content, private_names):
            errors.append(f'{path.relative_to(root)}: internal identifier')
        if UNRESOLVED_TEMPLATE.search(content):
            errors.append(f'{path.relative_to(root)}: unresolved template expression')
        if path.suffix == '.md':
            for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', content):
                if target.startswith(('https://', 'http://')):
                    continue
                file, _, fragment = target.partition('#')
                dest = path.parent / file if file else path
                if not dest.is_file():
                    errors.append(f'{path.relative_to(root)}: broken link {target}')
                elif fragment:
                    try:
                        target_content = dest.read_text(encoding='utf-8')
                    except UnicodeDecodeError:
                        errors.append(f'{path.relative_to(root)}: link target is not UTF-8: {target}')
                        continue
                    headings = re.findall(r'^#+ (.+)$', target_content, re.M)
                    anchors = [re.sub(r'[^\w -]', '', h.lower()).replace(' ', '-') for h in headings]
                    if fragment not in anchors:
                        errors.append(f'{path.relative_to(root)}: broken anchor {target}')
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='render REPORT.md from reviewed summary.json')
    args = parser.parse_args()
    if args.write:
        for name in discover():
            directory = ROOT / 'reports' / name
            summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
            (directory / 'REPORT.md').write_bytes(render(summary).encode('utf-8'))
    failures = check()
    if failures:
        raise SystemExit('\n'.join(failures))
    print(f'Reports passed ({len(discover())} reports, {len(discover(kind="insight"))} studies): scrub, summary '
          'rendering, dashboards, calibration, catalog hashes and links')
