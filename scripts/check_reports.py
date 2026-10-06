"""Offline integrity gates for published reports; no private source checkout needed."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ('2026-09-29-web-search-bakeoff', '2026-10-03-search-gap-bench', '2026-10-05-agent-search-behavior',
           '2026-09-26-search-api-head-to-head')
# Private infrastructure and credentials are never valid report content.
# File URIs need an explicit branch: the path boundary below permits web URLs.
FORBIDDEN = re.compile(
    r'op://' r'|(?<![\w/:])(?:/Users/|/home/|/private/|/Volumes/|[A-Z]:\\Users\\)'
    r'|file://[^\s)\"]*/(?:Users|home|private|Volumes)/'
    r'|\bHQ_ROOT\b|https?://[^\s)\"]*/agent-os(?:/|\b)'
    r'|\b(?:LRQ[-_:]|lrq[-_:])\S+|\b(?:run_id|run-id|suite-run-id)\s*[:=]'
    r'|\.internal\b'
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


def check(root=ROOT):
    root = Path(root)
    errors = []
    # Optional private input, supplied outside Git; never print its values.
    private_names = frozenset(
        name.strip().casefold()
        for name in os.environ.get('SEW_REPORT_PRIVATE_NAMES', '').splitlines()
        if name.strip()
    )
    for name in REPORTS:
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
        if name == REPORTS[1]:
            if calibration['catalog_sha256'] != catalog['historical']['battery']:
                errors.append(f'{name}: calibration catalog hash differs from historical battery')
            table = next(b['table'] for b in data['blocks'] if 'table' in b and b['table'][0][0] == 'Task')
            for harness, col in [('claude-code', 2), ('codex', 3)]:
                for record, row in zip(calibration['harnesses'][harness]['tasks'], table[2:], strict=True):
                    admitted = (
                        record['floor_passes'] <= calibration['floor_max_passes']
                        and record['ceiling_passes'] >= calibration['ceiling_min_passes']
                    )
                    if record['admitted'] is not admitted:
                        errors.append(f'{name}: {harness}/{record["task"]}: admission violates calibration thresholds')
                    expected = f"Floor: {record['floor_passes']}, Ceiling: {record['ceiling_passes']} — " + ('admitted' if record['admitted'] else 'rejected')
                    if row[col] != expected or row[0] != record['task'] or row[1] != record['family']:
                        errors.append(f'{name}: calibration does not match summary')
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
        for name in REPORTS:
            directory = ROOT / 'reports' / name
            summary = json.loads((directory / 'summary.json').read_text(encoding='utf-8'))
            (directory / 'REPORT.md').write_bytes(render(summary).encode('utf-8'))
    failures = check()
    if failures:
        raise SystemExit('\n'.join(failures))
    print('Reports passed: scrub, summary rendering, calibration, catalog hashes and links')
