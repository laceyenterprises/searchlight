"""The design system, as the generator uses it: tokens and site styles, the glyph sprite, the marks, the vocabulary."""
from __future__ import annotations

import sys
from pathlib import Path

from .data import ROOT

DESIGN = ROOT / 'design'


SITE_ASSETS = ROOT / 'scripts' / 'site_assets'
sys.path.insert(0, str(DESIGN / 'glyphs'))
import searchlight_glyphs as glyphs  # noqa: E402

# The header mark (A, lamp) and the favicon (C, tile). design/glyphs/glyphs.html shows every option.


LOGO_ID = 'logo-lamp'


FAVICON_ID = 'logo-tile'


LOGO = glyphs.logo_svg(FAVICON_ID)


def _read(path: Path) -> str:
    return path.read_text(encoding='utf-8')


# The card is drawn on the night ground: the mark's ink is white and its lamp hole is the card's ground.


TOKENS = _read(DESIGN / 'tokens.css')


CSS = TOKENS + _read(SITE_ASSETS / 'site.css')


JS = _read(SITE_ASSETS / 'site.js')


SPRITE = glyphs.sprite().replace('style="display:none"', 'aria-hidden="true" focusable="false" '
                                 'style="position:absolute;width:0;height:0;overflow:hidden"')


def g(glyph: str, cls: str = '') -> str:
    """A glyph from the sprite, decorative: the words beside it carry the meaning."""
    return f'<svg class="g{" " + cls if cls else ""}" aria-hidden="true" focusable="false"><use href="#{glyph}"/></svg>'


def mark(cls: str = 'mark') -> str:
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><use href="#{LOGO_ID}"/></svg>'


def vocabulary() -> dict[str, list[list[str]]]:
    """The tables in design/vocabulary.md, by section, without their header rows."""
    out: dict[str, list[list[str]]] = {}
    section = None
    for line in _read(DESIGN / 'vocabulary.md').splitlines():
        if line.startswith('## '):
            section = line[3:].strip()
            out[section] = []
        elif section and line.startswith('|') and not set(line) <= set('|-: '):
            out[section].append([cell.strip() for cell in line.strip().strip('|').split('|')])
    return {name: rows[1:] for name, rows in out.items() if rows}
