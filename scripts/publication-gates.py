"""Fail closed publication checks for the tracked tree, reachable history and dependencies."""
import argparse
import gzip
import io
import json
import tarfile
import zipfile
import importlib.metadata as metadata
import os
import re
import subprocess
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

# Generic portable patterns: no operator identities or scanner allowlists.
HARDCODES = {
    'account-path': r'/(?:Users|home)/[a-z][a-z0-9_-]+/(?:\.openclaw|agent-os|agent-os-hq)',
    'private-repository': r'github\.com[/:][^/\s]+/agent-os(?![A-Za-z0-9_-])',
    'private-vault': r'op://[^\s\x22\x27<>]+/[^\s\x22\x27<>]+/',
    'private-network': r'\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+\b',
}
SECRETS = {
    'private-key': r'-----BEGIN (?:(?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----',
    'github-token': r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b',
    'provider-key': r'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b|\b(?:tvly-|pplx-|fc-|sk_live_|xox[bap]-)[A-Za-z0-9_-]{24,}\b|\bAIza[A-Za-z0-9_-]{35}\b',
    'bearer-token': r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]{24,}=*',
    'aws-key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'credential-assignment': r'''(?i)(?:api[_-]?key|secret|password|access[_-]?token)["']?\s*[=:]\s*["']?([A-Za-z0-9+/=_-]{24,})(?:["']|\s|$)''',
}
PERMISSIVE = {'MIT', 'BSD-2-Clause', 'BSD-3-Clause', 'Apache-2.0', 'ISC', '0BSD',
              'Unlicense', 'CC0-1.0', 'Python-2.0', 'PSF-2.0'}
LEGACY = {'MIT License': 'MIT', 'BSD License': 'BSD-3-Clause',
          'Apache Software License': 'Apache-2.0', 'Apache-2.0': 'Apache-2.0',
          'Python Software Foundation License': 'PSF-2.0',
          'ISC License (ISCL)': 'ISC'}


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


MAX_SCAN_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 256


def findings(data, patterns, *, depth=0, budget=None):
    # Bound decompression without extracting paths or executing archive members.
    budget = [MAX_SCAN_BYTES] if budget is None else budget
    budget[0] -= len(data)
    if budget[0] < 0 or depth > 4:
        return ['archive-limit']
    try:
        if data.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                expanded = stream.read(budget[0] + 1)
            return findings(expanded, patterns, depth=depth + 1, budget=budget)
        if data.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    return ['archive-limit']
                matches = []
                for member in members:
                    if member.file_size > budget[0]:
                        return ['archive-limit']
                    with archive.open(member) as stream:
                        matches.extend(findings(stream.read(budget[0] + 1), patterns,
                                                depth=depth + 1, budget=budget))
                return sorted(set(matches))
        if data[257:262] == b'ustar' or data.startswith((b'BZh', b'\xfd7zXZ\x00')):
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
                matches = []
                for index, member in enumerate(archive):
                    if index >= MAX_ARCHIVE_MEMBERS or member.size > budget[0]:
                        return ['archive-limit']
                    if member.isfile():
                        with archive.extractfile(member) as stream:
                            matches.extend(findings(stream.read(budget[0] + 1), patterns,
                                                    depth=depth + 1, budget=budget))
                    elif member.issym() or member.islnk():
                        matches.extend(findings(member.linkname.encode(), patterns,
                                                depth=depth + 1, budget=budget))
                return sorted(set(matches))
    except (OSError, EOFError, RuntimeError, ValueError, zipfile.BadZipFile, tarfile.TarError):
        return ['archive-error']
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8'
    text = data.decode(encoding, errors='replace')
    matches = []
    for name, pattern in patterns.items():
        for match in re.finditer(pattern, text):
            # Exact synthetic scrub values do not exempt adjacent real keys.
            if name == 'provider-key' and match.group() == 'sk-' + 'ant-api03-' + 'SECRET' * 4:
                continue
            if name == 'bearer-token' and match.group().lower() in {
                    'bearer sk-' + 'ant-api03-' + 'secret' * 4,
                    'bearer sk-' + 'ant-api03-' + 'abcdefghij1234567890',
                    'bearer ' + 'abcdefghijklmnopqrstuvwxyz0123456789'}:
                continue
            if name == 'credential-assignment' and match.group(1) == 'synthetic-' + 'credential-for-test':
                continue
            matches.append(name)
            break
    return matches


def tree_audit(root, patterns):
    failures = []
    for raw in git(root, 'ls-files', '-z').split(b'\0'):
        if raw:
            path = raw.decode()
            # Read the staged blob, including a symlink's target text. Host files
            # and unstaged edits must not mask what is about to be published.
            failures.extend(f'{path}: {name}' for name in findings(
                git(root, 'cat-file', '-p', f':{path}'), patterns))
    return failures


def history_audit(root, patterns=SECRETS):
    if git(root, 'rev-parse', '--is-shallow-repository').strip() == b'true':
        raise ValueError('history audit requires a full clone')
    if os.environ.get('GITHUB_ACTIONS') == 'true' and not git(
            root, 'for-each-ref', '--format=%(refname)', 'refs/remotes/pull', 'refs/pull').strip():
        raise ValueError('CI history audit requires fetched pull-request refs (refs/remotes/pull/*)')
    objects = [entry.split(b' ', 1)[0] for entry in git(root, 'rev-list', '--objects', '--all').splitlines()]
    objects.extend(git(root, 'for-each-ref', '--format=%(objectname)', 'refs/tags').splitlines())
    objects = list(dict.fromkeys(objects))
    if not objects:
        raise ValueError('history audit requires reachable commits')
    failures = []
    # One batch Git process replaces two subprocesses per historical object.
    with subprocess.Popen(['git', '-C', str(root), 'cat-file', '--batch'],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as process:
        for oid in objects:
            process.stdin.write(oid + b'\n')
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3:
                raise ValueError('history object could not be read')
            _, kind, size = header
            size = int(size)
            if size > MAX_SCAN_BYTES:
                # Drain in bounded chunks; oversized blobs cannot silently pass.
                remaining = size
                while remaining:
                    block = process.stdout.read(min(remaining, 65536))
                    if not block:
                        raise ValueError('truncated history object')
                    remaining -= len(block)
                if kind in {b'blob', b'commit', b'tag'}:
                    failures.append(f'{oid.decode()}: archive-limit')
            else:
                data = process.stdout.read(size)
                if len(data) != size:
                    raise ValueError('truncated history object')
                if kind in {b'blob', b'commit', b'tag'}:
                    failures.extend(f'{oid.decode()}: {name}' for name in findings(data, patterns))
            if process.stdout.read(1) != b'\n':
                raise ValueError('invalid batch history framing')
        process.stdin.close()
        if process.wait() != 0:
            raise ValueError('history batch process failed')
    return failures


def licence_allowed(info):
    expression = info.get('License-Expression')
    if expression:
        tokens = re.findall(r'[A-Za-z0-9.+-]+|[()]', expression)
        if ''.join(tokens) != re.sub(r'\s+', '', expression):
            return False
        operand, depth = True, 0
        for token in tokens:
            if operand:
                if token == '(':
                    depth += 1
                elif token in PERMISSIVE:
                    operand = False
                else:
                    return False
            elif token == ')' and depth:
                depth -= 1
            elif token in {'AND', 'OR'}:
                operand = True
            else:
                return False
        # Every branch must be permissive; exceptions and unknown identifiers fail.
        return bool(tokens) and not operand and depth == 0
    licence = info.get('License', '').strip()
    classifiers = [value.rsplit(' :: ', 1)[-1] for value in info.get_all('Classifier', [])
                   if value.startswith('License :: OSI Approved :: ')]
    if classifiers:
        return all(value in LEGACY for value in classifiers)
    return licence in PERMISSIVE or licence in LEGACY


def supported_environments():
    for platform, system in [('linux', 'Linux'), ('darwin', 'Darwin')]:
        for version in ['3.11', '3.12', '3.13']:
            yield {**default_environment(), 'sys_platform': platform,
                   'platform_system': system, 'os_name': 'posix',
                   'python_version': version, 'python_full_version': version + '.0'}


def licence_audit():
    pending = [('searchlight', ('test',))]
    seen = set()
    failures = []
    while pending:
        name, extras = pending.pop()
        key = (canonicalize_name(name), tuple(sorted(extras)))
        if key in seen:
            continue
        seen.add(key)
        dist = metadata.distribution(name)
        if not licence_allowed(dist.metadata):
            failures.append(f'{name}=={dist.version}: missing or non-permissive licence')
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or any(requirement.marker.evaluate({**environment, 'extra': extra})
                                                 for environment in supported_environments()
                                                 for extra in ('', *extras)):
                pending.append((requirement.name, tuple(requirement.extras)))
    print(f'Licence audit: {len(seen)} public runtime/test dependency records')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gate', choices=['hardcodes', 'secrets', 'licences'])
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        if args.gate == 'hardcodes':
            failures = tree_audit(args.root, HARDCODES)
            history = history_audit(args.root, HARDCODES)
            report = args.root / 'publication-hardcode-history.json'
            report.write_text(json.dumps({'requires_visibility_decision': bool(history),
                                          'findings': history}, indent=2) + '\n')
            print(f'Historical hardcodes: {len(history)}; visibility decision report: {report.name}')
        elif args.gate == 'secrets':
            failures = tree_audit(args.root, SECRETS) + history_audit(args.root)
        else:
            failures = licence_audit()
    except (ValueError, subprocess.CalledProcessError, metadata.PackageNotFoundError) as exc:
        print(f'Publication gate error: {exc}')
        return 2
    for failure in failures:
        print(failure)  # Never print matched credential contents.
    print(f'{args.gate}: {len(failures)} findings')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
