#!/usr/bin/env python3
"""Render site/social-card.html to site/social-card.png with headless Chrome.

The PNG carries the SHA-256 of the card source it was rendered from, so
`python3 scripts/build_site.py --check` can tell a stale image from a current
one without a browser. Run after `python3 scripts/build_site.py` whenever the
check reports the card as stale. Needs Google Chrome or Chromium and network
access for the Inter web font. Set CHROME to the browser binary if it is not
found automatically.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import build_site  # noqa: E402

IEND = b'IEND\xaeB`\x82'
CANDIDATES = (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    'google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser',
)


def find_chrome() -> str:
    for candidate in ([os.environ['CHROME']] if os.environ.get('CHROME') else []) + list(CANDIDATES):
        path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if path and os.path.exists(path):
            return path
    raise SystemExit('Chrome or Chromium not found; set CHROME to the browser binary')


def render(source: Path, chrome: str, timeout: float = 120.0) -> bytes:
    """Screenshot the card. Headless Chrome on macOS can keep running after it writes the
    screenshot, so wait for a complete PNG and then stop the browser's process group."""
    width, height = build_site.CARD_SIZE
    with tempfile.TemporaryDirectory() as profile:
        shot = Path(profile) / 'card.png'
        proc = subprocess.Popen([chrome, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
                                 '--no-default-browser-check', '--disable-background-networking',
                                 f'--user-data-dir={profile}', f'--force-device-scale-factor={build_site.CARD_SCALE}',
                                 f'--window-size={width},{height}', '--virtual-time-budget=10000',
                                 f'--screenshot={shot}', source.resolve().as_uri()],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if shot.is_file() and shot.read_bytes().endswith(IEND):
                    return shot.read_bytes()
                if proc.poll() is not None and not shot.is_file():
                    raise SystemExit(f'Chrome exited ({proc.returncode}) without a screenshot')
                time.sleep(0.25)
            raise SystemExit(f'no screenshot after {timeout:.0f} s')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--site', type=Path, default=ROOT / 'site')
    args = parser.parse_args(argv)
    source = args.site / 'social-card.html'
    if not source.is_file():
        raise SystemExit(f'{source} not found; run python3 scripts/build_site.py first')
    text = source.read_text(encoding='utf-8')
    data = render(source, find_chrome())
    expected = tuple(n * build_site.CARD_SCALE for n in build_site.CARD_SIZE)
    if build_site.png_size(data) != expected:
        raise SystemExit(f'rendered {build_site.png_size(data)}, expected {expected}')
    stamped = build_site.stamp_png(data, build_site.CARD_KEY, build_site.card_source_hash(text))
    (args.site / 'social-card.png').write_bytes(stamped)
    print(f'Rendered site/social-card.png ({expected[0]}x{expected[1]}, {len(stamped) // 1024} KiB)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
