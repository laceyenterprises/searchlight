"""The README art: the site's design drawn as standalone SVGs for GitHub, written to docs/readme/.

GitHub shows README images through <img>, so each file carries what a site page would supply: one theme's design
tokens, the drawing styles, the glyph symbols it uses and a system font stack. Nothing loads from outside the file.
Every image has a light and a dark variant (the README picks one with <picture>), and the ones with text also have a
tall layout for phones. Numbers come from the committed reports through the same data module the site uses.
"""
from __future__ import annotations

import dataclasses
import re

from . import diagrams as dg
from .components import ROW_NOTES, board_says, report_date, site_map
from .data import (ROOT, BENCH_LABEL, GAP_PRIMARY, REPORT_TITLES, data_rows, esc, gap_boards, harness_name, headline,
                   parse_pct_ci, tables, wilson)
from .design import LOGO_ID, TOKENS, glyphs, vocabulary
from .pages import BEAM_ART

OUT = 'docs/readme'
WIDE, TALL = 840, 360  # README art: 840 for an 830 px column; 360 for phones, shown 326 to 358 px wide
BOTTOM = 16  # clear space under each image, so the text after it does not sit on its edge
SYSTEM = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif'
MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'
THEMES = ('light', 'dark')


def _vars(block: str) -> dict[str, str]:
    return dict(re.findall(r'(--[\w-]+):\s*([^;]+);', block))


_LIGHT = _vars(re.search(r':root \{(.*?)\n\}', TOKENS, re.S).group(1))
_DARK = {**_LIGHT, **_vars(re.search(r':root\[data-theme="dark"\] \{(.*?)\}', TOKENS, re.S).group(1))}
THEME = {theme: ':root{' + ';'.join(f'{k}:{v.strip()}' for k, v in values.items())
         + f';--font-body:{SYSTEM};--font-display:{SYSTEM};--font-mono:{MONO}}}'
         for theme, values in (('light', _LIGHT), ('dark', _DARK))}

# The diagram styles are the site's own (scripts/site_assets/site.css), minus what only places them on a page.
_SITE_CSS = (ROOT / 'scripts' / 'site_assets' / 'site.css').read_text(encoding='utf-8')
_BLOCK = _SITE_CSS[_SITE_CSS.index('/* Methodology diagrams'):_SITE_CSS.index('.arrives')]
FLOW_CSS = ''.join(f'{sel.strip()}{{{body}}}' for sel, body in re.findall(r'\.flow ([^{]+)\{([^}]*)\}', _BLOCK)
                   if 'display' not in body and 'max-width' not in body)

# The README's own pieces, in the site's recipes: ranked rows, tiles, chips and the night hero.
ART_CSS = """
.pn{fill:var(--soft)}.cd{fill:var(--raised);stroke:var(--rule);stroke-width:1.5}.hr{stroke:var(--rule);stroke-width:1.5}
.ey{font:500 15px var(--font-mono);letter-spacing:.08em;fill:var(--muted)}
.hd{font:700 21px var(--font-display);letter-spacing:-.01em;fill:var(--ink)}
.mt{font:400 16px var(--font-body);fill:var(--muted)}
.say{font:400 17px var(--font-body);fill:var(--ink-2)}
.rk{font:600 16px var(--font-mono);fill:var(--muted)}
.nm{font:650 19px var(--font-body);fill:var(--ink)}.nm.ctl{font-weight:500;fill:var(--ink-2)}
.pc{font:750 30px var(--font-display);letter-spacing:-.03em;fill:var(--ink);font-variant-numeric:tabular-nums}
.pc.ctl{fill:var(--ink-2)}
.rg{font:500 16px var(--font-body);fill:var(--ink-2);font-variant-numeric:tabular-nums}
.kn{font:400 15px var(--font-body);fill:var(--muted);font-variant-numeric:tabular-nums}
.tk{font:500 15px var(--font-mono);fill:var(--muted)}
.trk{fill:var(--viz-track)}.ci{fill:var(--viz-interval)}.ci.ctl{fill:var(--viz-other);opacity:.55}
.dot{fill:var(--blue);stroke:var(--paper);stroke-width:2}.dot.ctl{fill:var(--viz-ref)}
.lb{stroke:var(--ink-2);stroke-width:2;stroke-dasharray:4 3;opacity:.55}
.chip{fill:var(--amber-soft)}.chipt{font:600 15px var(--font-body);fill:var(--amber-ink)}
.ctlchip{fill:var(--paper)}.ctlt{font:600 15px var(--font-body);fill:var(--ink-2)}
.tn{font:650 19px var(--font-body);fill:var(--ink)}.tb{font:400 16px var(--font-body);fill:var(--ink-2)}
.gid{font:500 15px var(--font-mono);fill:var(--ink-2)}
.night{fill:var(--night)}.nf{fill:var(--night-fg)}.nmu{fill:var(--night-mute)}.nb{fill:#7DBBFF}
.word{font:700 26px var(--font-display);letter-spacing:-.02em}
.hq{font:750 36px var(--font-display);letter-spacing:-.03em}
.ha{font:400 20px var(--font-body)}.ha .nb{font-weight:700}
.hey{font:500 15px var(--font-mono);letter-spacing:.08em}
.dt{font:500 15px var(--font-mono);fill:var(--muted)}.ax{stroke:var(--rule);stroke-width:2}
.stop{fill:var(--raised);stroke:var(--rule);stroke-width:1.5}.stop.new{stroke:var(--blue)}
use{color:var(--ink)}
.tall .tk{font-size:16px}.tall .chipt{font-size:16px}.tall .ctlt{font-size:16px}.tall .kn{font-size:16px}
.tall .gid{font-size:16px}.tall .dt{font-size:16px}.tall .hey{font-size:16px}.tall .hq{font-size:30px}
"""
SMALL = {'wide': 15, 'tall': 16}  # the smallest type in each layout: chips, ticks, ids and dates

_SYMBOLS = {g[0]: glyphs.SYMBOL.format(id=g[0], body=g[3]) for g in glyphs.GLYPHS}
_SYMBOLS.update({g[0]: glyphs.SYMBOL.format(id=g[0], body=g[2]) for g in glyphs.LOGOS})


def _css_for(svg: str) -> str:
    """The style rules this drawing uses: a rule stays when every class its selector names is on some element."""
    classes = {c for attr in re.findall(r'class="([^"]*)"', svg) for c in attr.split()}
    rules = re.findall(r'([^{}]+)\{([^}]*)\}', FLOW_CSS + ART_CSS)
    return ''.join(f'{sel.strip()}{{{body}}}' for sel, body in rules
                   if set(re.findall(r'\.([\w-]+)', sel)) <= classes)


def standalone(svg: str, theme: str) -> str:
    """An SVG drawn with the site's classes and sprite, made self-contained for an <img> on GitHub."""
    m = re.match(r'<svg ([^>]*?)viewBox="0 0 ([\d.]+) ([\d.]+)"([^>]*)>', svg)
    w, h = m.group(2), m.group(3)
    if w != '24':  # not a glyph set in a line of text
        h = f'{float(h) + BOTTOM:.0f}'
    used = sorted({i for i in re.findall(r'href="#([\w-]+)"', svg) if i in _SYMBOLS})
    cls = re.search(r'class="([^"]*)"', m.group(0)).group(1).split()
    sizes = dg.type_css('readme' if 'wide' in cls else 'readme-tall') if 'rd' in cls else ''
    return (f'<svg xmlns="http://www.w3.org/2000/svg" {m.group(1)}viewBox="0 0 {w} {h}" width="{w}" height="{h}"'
            f'{m.group(4)}><style>{THEME[theme]}{_css_for(svg)}{sizes}</style>'
            f'<defs>{"".join(_SYMBOLS[i] for i in used)}</defs>{svg[m.end():]}\n')


def _svg(cls: str, width: float, height: float, title: str, desc: str, body: str) -> str:
    return (f'<svg class="{cls}" viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-labelledby="t d">'
            f'<title id="t">{esc(title)}</title><desc id="d">{esc(desc)}</desc>{body}</svg>')


def _text(x: float, y: float, text: str, cls: str, anchor: str = '') -> str:
    a = f' text-anchor="{anchor}"' if anchor else ''
    return f'<text class="{cls}" x="{x:.1f}" y="{y:.1f}"{a}>{esc(text)}</text>'


ON_NIGHT = 'color:var(--night-fg);--paper:var(--night)'  # a mark on the night ground: white ink, a night lamp hole


def _glyph(gid: str, x: float, y: float, size: float = 24, style: str = '') -> str:
    s = f' style="{style}"' if style else ''
    return f'<use href="#{gid}" x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}"{s}/>'


def _wrap(text: str, width: float, size: float) -> list[str]:
    return dg.wrap(text, width, size, 'b')


def _lines(x: float, y: float, lines: list[str], cls: str, lh: float, anchor: str = '') -> str:
    return ''.join(_text(x, y + i * lh, line, cls, anchor) for i, line in enumerate(lines))


# ---------------------------------------------------------------- ranked rows with 95% ranges

def _chip(x: float, y: float, label: str, kind: str = 'caveat', size: int = 14) -> tuple[str, float]:
    """A note chip beside a name (amber: a caveat applies; grey: a control). Returns (svg, width)."""
    w = dg._width(label, size, 'b') + (34 if kind == 'caveat' else 20)
    body = f'<rect class="{"chip" if kind == "caveat" else "ctlchip"}" x="{x:.1f}" y="{y - 17:.1f}" width="{w:.1f}" height="24" rx="12"/>'
    if kind == 'caveat':
        body += _glyph('caveat', x + 6, y - 14, 18) + _text(x + 28, y, label, 'chipt')
    else:
        body += _text(x + 10, y, label, 'ctlt')
    return body, w


def _track(x0: float, x1: float, cy: float, r: dict, scale: float, lower_bound: float | None) -> str:
    def at(v):
        return x0 + (x1 - x0) * max(0.0, min(v, scale)) / scale
    ctl = ' ctl' if r.get('control') else ''
    out = [f'<rect class="trk" x="{x0:.1f}" y="{cy - 7:.1f}" width="{x1 - x0:.1f}" height="14" rx="7"/>',
           f'<rect class="ci{ctl}" x="{at(r["lo"]):.1f}" y="{cy - 7:.1f}" width="{max(6.0, at(r["hi"]) - at(r["lo"])):.1f}" '
           'height="14" rx="7"/>']
    if lower_bound is not None:
        lx = at(lower_bound)
        out.append(f'<path class="lb" d="M{lx:.1f} {cy - 12:.1f}V{cy + 12:.1f}"/>')
    out.append(f'<circle class="dot{ctl}" cx="{at(r["point"]):.1f}" cy="{cy:.1f}" r="7"/>')
    return ''.join(out)


def _scale(x0: float, x1: float, y: float, scale: float, unit: str) -> str:
    ticks = [(x0, f'0{unit}', 'start'), ((x0 + x1) / 2, f'{scale / 2:.0f}{unit}', 'middle'), (x1, f'{scale:.0f}{unit}', 'end')]
    return ''.join(_text(x, y, label, 'tk', anchor) for x, label, anchor in ticks)


def ranked(block: dict, layout: str, y: float, x0: float, x1: float) -> tuple[str, float]:
    """One board as ranked rows: rank, name (and its note), the big number, the 0-scale track with the 95% range and a
    dot, then the range and n. Wide rows are one line; tall rows put the track on a second line."""
    out, scale, unit = [], block['scale'], block.get('unit', '')
    title = _wrap(block['title'], x1 - x0, 21)
    out.append(_lines(x0, y + 21, title, 'hd', 27))
    y += 21 + 27 * (len(title) - 1)
    meta = _wrap(block['meta'], x1 - x0, 16)
    out.append(_lines(x0, y + 26, meta, 'mt', 22))
    y += 26 + 22 * (len(meta) - 1) + 14
    wide, small = layout == 'wide', SMALL[layout]
    # The big number sits just right of the longest name (and its glyph or chip), never on it.
    name_w = max(dg._width(r['label'], 19, 't') + (28 if r.get('glyph') else 0)
                 + (dg._width(r['chip'][0], small, 'b') + 44 if r.get('chip') else 0) for r in block['rows'])
    pc_x = max(x0 + 300, x0 + 30 + name_w + 16 + max(dg._width(r['value'], 30, 't') for r in block['rows']))
    tx0, tx1 = (pc_x + 22, x1 - 118) if wide else (x0 + 30, x1 - 104)
    out.append(_scale(tx0, tx1, y + 18, scale, unit))
    y += 30
    for r in block['rows']:
        out.append(f'<path class="hr" d="M{x0:.1f} {y:.1f}H{x1:.1f}"/>')
        ctl = ' ctl' if r.get('control') else ''
        name_x = x0 + 30 + (28 if r.get('glyph') else 0)
        base = y + 36 if wide else y + 30
        out.append(_text(x0 + 18, base - 1, str(r['rank']), 'rk', 'end'))
        if r.get('glyph'):
            out.append(_glyph(r['glyph'], x0 + 30, base - 18, 22))
        out.append(_text(name_x, base, r['label'], f'nm{ctl}'))
        if r.get('chip'):
            chip, _ = _chip(name_x + dg._width(r['label'], 19, 't') + 10, base - 1, *r['chip'], small)
            out.append(chip)
        if wide:
            out.append(_text(pc_x, y + 39, r['value'], f'pc{ctl}', 'end'))
            out.append(_track(tx0, tx1, y + 28, r, scale, block.get('lower_bound')))
            out.append(_text(x1, y + 24, r['range'], 'rg', 'end'))
            out.append(_text(x1, y + 45, r['n'], 'kn', 'end'))
            y += 58
        else:
            out.append(_text(x1, y + 34, r['value'], f'pc{ctl}', 'end'))
            out.append(_track(tx0, tx1, y + 60, r, scale, block.get('lower_bound')))
            out.append(_text(x1, y + 62, r['range'], 'rg', 'end'))
            out.append(_text(x1, y + 83, r['n'], 'kn', 'end'))
            y += 98
    out.append(f'<path class="hr" d="M{x0:.1f} {y:.1f}H{x1:.1f}"/>')
    for glyph, text in (('tie', block['say']), ('repeat', block.get('note', ''))):
        if text:
            lines = _wrap(text, x1 - x0 - 34, 17)
            out.append(_glyph(glyph, x0, y + 14, 24) + _lines(x0 + 34, y + 33, lines, 'say', 24))
            y += 24 * len(lines) + 12
    return ''.join(out), y + 20


def _legend(x0: float, y: float, layout: str) -> tuple[str, float]:
    keys = [('dot', 'Pass rate'), ('ci', '95% range'), ('lb', "Where the leader's range starts")]
    out, x = [], x0
    for kind, label in keys:
        w = 34 + dg._width(label, 16, 'b')
        if layout == 'tall' and x > x0 and x + w > TALL - 16:  # wrap the key on a phone
            x, y = x0, y + 28
        if kind == 'dot':
            out.append(f'<circle class="dot" cx="{x + 8:.1f}" cy="{y - 6:.1f}" r="7"/>')
        elif kind == 'ci':
            out.append(f'<rect class="ci" x="{x:.1f}" y="{y - 13:.1f}" width="24" height="14" rx="7"/>')
        else:
            out.append(f'<path class="lb" d="M{x + 8:.1f} {y - 17:.1f}V{y + 5:.1f}" style="opacity:1"/>')
        out.append(_text(x + 32, y, label, 'mt'))
        x += w + 22
    return ''.join(out), y


def _panel(blocks: list[dict], layout: str, title: str, desc: str, legend: bool = True) -> str:
    width = WIDE if layout == 'wide' else TALL
    pad = 28 if layout == 'wide' else 16
    x0, x1 = pad, width - pad
    out, y = [], pad + 6
    if legend:
        keys, y = _legend(x0, y + 14, layout)
        out.append(keys)
        y += 22
    for i, block in enumerate(blocks):
        if i:
            out.append(f'<path class="hr" d="M0 {y + 8:.1f}H{width}" style="stroke-width:3"/>')
            y += 30
        svg, y = ranked(block, layout, y, x0, x1)
        out.append(svg)
    height = y + pad - 12
    return _svg(layout, width, height, title, desc,
                f'<rect class="pn" width="{width}" height="{height:.0f}" rx="16"/>' + ''.join(out))


def _floor_row(board: dict) -> dict:
    """A gap board's no-search reference as a control row: its published range, or Wilson's from its count."""
    cells = board['rows'][0]['cells']
    cell = board['reference'].get('floor (no search)', '')
    try:
        point, lo, hi = parse_pct_ci(cell)
    except ValueError:
        point = float(re.match(r'\d+(?:\.\d+)?', cell).group())
        lo, hi = wilson(round(point * cells / 100), cells)
    return {'arm': 'no-search', 'label': 'No search', 'pass_pct': point, 'ci': [lo, hi],
            'passes': round(point * cells / 100), 'cells': cells}


def _board_block(board: dict, top: int, note: str = '') -> dict:
    agent, model = harness_name(board)
    rows = board['rows'] + ([_floor_row(board)] if board['bench'] == 'GAP' else [])
    shown = [(i, r) for i, r in enumerate(rows, start=1) if i <= top or r['arm'] == 'no-search']
    out = []
    for i, r in shown:
        row_note = ROW_NOTES.get(r.get('note'))
        chip = None
        if r['arm'] == 'no-search':
            chip = ('control', 'control')
        elif row_note:
            chip = (row_note[0], 'caveat')
        out.append({'rank': i if r in board['rows'] else '', 'label': r['label'], 'value': f'{r["pass_pct"]:.0f}%', 'lo': r['ci'][0], 'hi': r['ci'][1],
                     'point': r['pass_pct'], 'range': f'{r["ci"][0]:.0f}–{r["ci"][1]:.0f}%',
                     'n': f'{r["passes"]} of {r["cells"]}', 'control': r['arm'] in ('no-search', 'native'), 'chip': chip})
    bench = BENCH_LABEL.get(board['bench'], board['bench'])
    meta = [model, f'{board["rows"][0]["cells"]} runs per setup', report_date(board['source'])]
    return {'title': f'{bench} · {agent}', 'meta': ' · '.join(m for m in meta if m), 'rows': out,
            'scale': 100, 'unit': '%', 'lower_bound': board.get('top_lower_bound'), 'say': board_says(board),
            'note': note}


def _rows_desc(blocks: list[dict]) -> str:
    """The chart in words, every number included: it is also the image's alt text in the READMEs."""
    return ' '.join(f'{b["title"].replace(" · ", ", ")} ({b["meta"]}): '
                    + '; '.join(f'{r["label"]} {r["value"]} (95% range {r["range"]}, {r["n"]})' for r in b['rows'])
                    + f'. {b["say"]}' + (f' {b["note"]}' if b.get('note') else '') for b in blocks)


def replication(reports: dict, board: dict) -> str:
    """How the board's leader did when its battery ran again on the same tasks, from the replication's comparison table."""
    leader = board['rows'][0]['label']
    for name, report in sorted(reports.items()):
        if (board['bench'] != 'GAP' or name <= board['source'] or report['summary'].get('bench') != 'GAP'
                or board['harness'] not in {b['harness'] for b in gap_boards(name, report['summary'], primary=False)}):
            continue
        for _, table in tables(report['summary']):
            head = table[0]
            row = next((r for r in data_rows(table) if r[0] == leader), None)
            if row and len(head) > 3 and head[3].startswith('Fisher'):
                same = re.search(r'\d+ tasks', head[2])
                return (f'Run again on {report_date(name)} on the same {same.group() if same else "tasks"}, {leader} went '
                        f'from {row[1].split(" ")[0]} to {row[2].split(" ")[0]} ({head[3]}, {row[3]}).')
    return ''


def results(boards: list[dict], reports: dict) -> dict[str, str]:
    """The results snapshot: each benchmark's top setups as ranked rows with their 95% ranges, no search as the
    control, its tie sentence and, where the battery was run again, how the leader did."""
    order = [b for b in boards if b['source'] == GAP_PRIMARY] + [b for b in boards if b['bench'] == 'WSB']
    blocks = [_board_block(b, 4, replication(reports, b)) for b in order]
    title = 'Searchlight results: pass rates with their 95% ranges'
    return {layout: _panel(blocks, layout, title, _rows_desc(blocks)) for layout in ('wide', 'tall')}


def study_results(study: dict) -> dict[str, str]:
    """The newest insight study's top services, ranked by useful findings per question, with their 95% ranges."""
    from sew.insight.dashboard import ranked as study_rows, takeaway

    summary = study['summary']
    rows = study_rows(summary)
    scale = max([r['hi'] for r in rows] + [1.0])
    scale = 10 * -(-scale // 10)
    shown = [{'rank': i, 'label': r['arm'], 'value': f'{r["value"]:.1f}', 'lo': r['lo'], 'hi': r['hi'],
              'point': r['value'], 'range': f'{r["lo"]:.1f}–{r["hi"]:.1f}', 'n': f'{r["questions"]} questions',
              'glyph': {'research': 'answer', 'agent': 'agent'}.get(r['kind'], 'results-list')}
             for i, r in enumerate(rows[:5], start=1)]
    block = {'title': 'Useful findings per question', 'rows': shown, 'scale': scale, 'say': takeaway(summary),
             'meta': f'{summary["set"]["title"]} · {len(summary["arms"])} services · '
                     f'{summary["set"]["questions"]} questions · {report_date(study["name"])}'}
    title = f'Insight study results: {summary["set"]["title"]}'
    return {layout: _panel([block], layout, title, _rows_desc([block]), legend=False) for layout in ('wide', 'tall')}


# ---------------------------------------------------------------- the hero

def hero(boards: list[dict], analysis: dict) -> dict[str, str]:
    """The landing page's hero on its night ground: the mark, the one question and the answer with its numbers."""
    h = headline(boards, analysis)
    (lo, hi), floor = h['search'], h['floor']
    question = 'Does web search make AI agents better at real work?'
    parts = [('When the job is about something recent, search decides it: ', ''), (f'{floor:.0f}%', 'nb'), (' with no search, ', ''),
             (f'{lo:.0f}–{hi:.0f}%', 'nb'), (' with any provider.', '')]
    words = [(w, cls) for text, cls in parts for w in text.split(' ') if w]
    desc = f'Searchlight. {question} {"".join(t for t, _ in parts)}'
    out = {}
    for layout in ('wide', 'tall'):
        wide = layout == 'wide'
        width = WIDE if wide else TALL
        pad, text_w = (40, 470) if wide else (22, TALL - 44)
        body = [_glyph(LOGO_ID, pad, pad, 40, ON_NIGHT),
                f'<text class="word nf" x="{pad + 52}" y="{pad + 30}">Searchlight</text>']
        y = pad + 84
        body.append(_text(pad, y, 'AN OPEN BENCHMARK', 'hey nmu'))
        size = 36 if wide else 30
        q = dg.wrap(question, text_w, size * 1.04, 't')
        body.append(''.join(_text(pad, y + size + 8 + i * (size + 6), line, 'hq nf') for i, line in enumerate(q)))
        y += size + 8 + (size + 6) * (len(q) - 1) + 40
        lines, line, size = [], [], 20 if wide else 19
        for word, cls in words:
            trial = ' '.join(w for w, _ in line + [(word, cls)])
            if line and dg._width(trial, size, 'b') > text_w:
                lines.append(line)
                line = []
            line.append((word, cls))
        lines.append(line)
        for i, ln in enumerate(lines):
            spans = ' '.join(f'<tspan class="{c}">{esc(w)}</tspan>' if c else esc(w) for w, c in ln)
            body.append(f'<text class="ha nf" x="{pad}" y="{y + i * 30:.1f}">{spans}</text>')
        y += 30 * (len(lines) - 1)
        art = BEAM_ART[BEAM_ART.index('>') + 1:BEAM_ART.rindex('</svg>')]
        if wide:
            height = max(y + pad + 8, 300)
            scale = 0.78
            body.append(f'<g transform="translate({width - 420 * scale - 24:.1f} {(height - 300 * scale) / 2:.1f}) '
                        f'scale({scale})">{art}</g>')
        else:
            scale = 0.7
            body.append(f'<g transform="translate({(width - 420 * scale) / 2:.1f} {y + 16:.1f}) scale({scale})">{art}</g>')
            height = y + 16 + 300 * scale + pad
        out[layout] = _svg(layout, width, height, question, desc,
                           f'<rect class="night" width="{width}" height="{height:.0f}" rx="18"/>' + ''.join(body))
    return out


# ---------------------------------------------------------------- Searchlight in 60 seconds

def learn() -> dict[str, str]:
    """The overview's eight concept tiles: a glyph, the term and its one line, from design/vocabulary.md. Four columns
    wide; on a phone, one tile per row with the glyph beside the words."""
    concepts = vocabulary()['Concepts'][:8]
    desc = ' '.join(f'{term}: {one}' for term, _, one, _ in concepts)
    out = {}
    for layout, cols, width in (('wide', 4, WIDE), ('tall', 1, TALL)):
        wide = layout == 'wide'
        pad, gap = (20, 12) if wide else (12, 10)
        tw = (width - 2 * pad - gap * (cols - 1)) / cols
        text_x = 16 if wide else 60  # beside the glyph on a phone
        bodies = [_wrap(one, tw - text_x - 16, 16) for _, _, one, _ in concepts]
        body, y = [], pad
        for r in range(0, len(concepts), cols):
            lines = max(len(b) for b in bodies[r:r + cols])
            th = (16 + 32 + 14 + 22 + 8 + 22 * lines + 10) if wide else (16 + 22 + 8 + 22 * lines + 12)
            for c, (term, glyph, _, _) in enumerate(concepts[r:r + cols]):
                x = pad + c * (tw + gap)
                ty = y + 16 + 32 + 14 + 18 if wide else y + 16 + 18
                body.append(f'<rect class="cd" x="{x:.1f}" y="{y:.1f}" width="{tw:.1f}" height="{th:.1f}" rx="12"/>')
                body.append(_glyph(glyph, x + 16, y + 16, 32))
                body.append(_text(x + text_x, ty, term, 'tn'))
                body.append(_lines(x + text_x, ty + 26, bodies[r + c], 'tb', 22))
            y += th + gap
        height = y - gap + pad
        out[layout] = _svg(layout, width, height, 'Searchlight in 60 seconds', desc,
                           f'<rect class="pn" width="{width}" height="{height:.0f}" rx="16"/>' + ''.join(body))
    return out


# ---------------------------------------------------------------- the search setups

SETUPS = [dg.Node('no-search', 'no-search', 'No search', ['memory alone']),
          dg.Node('native', 'built-in', 'Built-in', ["the agent's own"]),
          dg.Node('brave', 'provider', 'Brave', ['MCP server']),
          dg.Node('tavily', 'provider', 'Tavily', ['MCP server']),
          dg.Node('exa', 'provider', 'Exa', ['MCP server']),
          dg.Node('parallel', 'provider', 'Parallel', ['hosted MCP']),
          dg.Node('firecrawl', 'provider', 'Firecrawl', ['MCP server']),
          dg.Node('perplexity', 'provider', 'Perplexity', ['MCP server'])]
REFERENCES = [dg.Node('floor', 'no-search', 'Floor', ['no search']),
              dg.Node('ceiling', 'source', 'Ceiling', ['the answer excerpt in the prompt'])]


def setups(boards: list[dict]) -> dict[str, str]:
    """Exactly one search setup per run: the agent, the eight setups it can be given, and the gap bench's two references.
    The agents' models are the ones the published boards name."""
    models = dict(harness_name(b) for b in boards if b['bench'] == 'GAP')
    named = ' or '.join(f'{agent} ({model})' for agent, model in models.items())
    agent_node = dg.Node('A', 'agent', 'The same agent, the same prompt',
                         [named, 'or Hermes Agent, Opencode or Pi on an OSS model'],
                         tall=[f'{" or ".join(models)}, or Hermes Agent, Opencode or Pi on an OSS model'])
    desc = (f'{agent_node.title}: {" ".join(agent_node.body)}. Each run gives it exactly one search setup: '
            + ', '.join(f'{n.title} ({n.body[0]})' for n in SETUPS)
            + '. The search gap bench adds two reference setups per task: floor, no search; ceiling, the answer excerpt '
              'in the prompt.')
    out = {}
    for layout in ('wide', 'tall'):
        t = dg.TYPE['readme' if layout == 'wide' else 'readme-tall']
        width = WIDE if layout == 'wide' else TALL
        cols, ref_cols = (4, 2) if layout == 'wide' else (2, 2)
        x0, x1 = 12, width - 12
        uid = f'fl-setups-{layout}'
        aw = 560 if layout == 'wide' else x1 - x0
        ax = (width - aw) / 2
        ah = dg._height(agent_node, aw, t)
        body = [dg._markers(uid), dg._node_svg(agent_node, (ax, 12, aw, ah), t)]
        y = 12 + ah + 56

        def grid(nodes, label, y, cols):
            gx0, gx1 = x0 + 10, x1 - 10
            cw = (gx1 - gx0 - 12 * (cols - 1)) / cols
            band = 14 + t['g'] + 4 + 10
            hs = [dg._height(n, cw, t) for n in nodes]
            rows = [max(hs[i:i + cols]) for i in range(0, len(nodes), cols)]
            parts = [f'<rect class="grp" x="{x0:.1f}" y="{y:.1f}" width="{x1 - x0:.1f}" '
                     f'height="{band + sum(rows) + 12 * (len(rows) - 1) + 10:.1f}" rx="16"/>',
                     f'<text class="gl" x="{x0 + 12:.1f}" y="{y + 13 + t["g"]:.1f}">{esc(label.upper())}</text>']
            cy = y + band
            for r, rh in enumerate(rows):
                for c, n in enumerate(nodes[r * cols:(r + 1) * cols]):
                    parts.append(dg._node_svg(n, (gx0 + c * (cw + 12), cy, cw, rh), t))
                cy += rh + 12
            return ''.join(parts), cy - 12 + 10
        g1, bottom = grid(SETUPS, 'Exactly one per run', y, cols)
        cx = width / 2
        body.append(f'<path class="e" d="M{cx:.1f} {12 + ah:.1f}V{y - 2:.1f}" marker-end="url(#{uid}-a)"/>')
        label = 'only this changes'
        lb = dg.label_box(label, (cx, 12 + ah + 26), t, upright=True)
        body.append(dg._label(label, lb, t))
        body.append(g1)
        g2, bottom = grid(REFERENCES, 'The search gap bench adds, per task' if layout == 'wide' else 'Gap bench adds',
                          bottom + 24, ref_cols)
        body.append(g2)
        out[layout] = _svg(f'{layout} rd', width, bottom + 12, 'Exactly one search setup per run', desc, ''.join(body))
    return out


# ---------------------------------------------------------------- the reports index

def reports_header(reports: dict, studies: list[dict]) -> dict[str, str]:
    """The reports index as a timeline: every report and study, oldest to newest, with its glyph and date."""
    pages = list(reversed(site_map(reports, studies)))
    sets = {s['name']: s['summary']['set'].get('set_id', '') for s in studies}
    entries = []
    for href, title, glyph in pages:
        name = href.removeprefix('report-').removeprefix('study-').removesuffix('.html')
        short = REPORT_TITLES.get(name) or title
        if sets.get(name):  # a study's short name is its question set's: software-factories-2026-11 -> Software factories
            short = re.sub(r'(-\d+)+$', '', sets[name]).replace('-', ' ').capitalize() + ' study'
        entries.append((report_date(name), short, glyph, href.startswith('study-')))
    runs = sum(r['cells'] for b in _boards(reports) for r in b['rows'])
    kinds = f'{len(reports)} reports and {len(studies)} {"study" if len(studies) == 1 else "studies"}'
    lead = f'{kinds} · {runs} graded runs on the leaderboard · every number transcribed from summary.json'
    desc = f'Searchlight reports. {lead}. ' + '; '.join(f'{d}: {t}' for d, t, _, _ in entries) + '.'
    out = {}
    for layout in ('wide', 'tall'):
        wide = layout == 'wide'
        width = WIDE if wide else TALL
        pad = 28 if wide else 16
        body = [_glyph(LOGO_ID, pad, pad, 36), _text(pad + 48, pad + 28, 'Searchlight reports', 'hd')]
        lead_lines = _wrap(lead, width - 2 * pad, 16)
        body.append(_lines(pad, pad + 64, lead_lines, 'mt', 22))
        y = pad + 64 + 22 * (len(lead_lines) - 1) + 30
        if wide:
            step = (width - 2 * pad) / len(entries)
            ay = y + 26
            body.append(f'<path class="ax" d="M{pad + step / 2:.1f} {ay:.1f}H{width - pad - step / 2:.1f}"/>')
            bottom = ay
            for i, (date, short, glyph, new) in enumerate(entries):
                cx = pad + step * i + step / 2
                body.append(f'<circle class="stop{" new" if new else ""}" cx="{cx:.1f}" cy="{ay:.1f}" r="24"/>')
                body.append(_glyph(glyph, cx - 13, ay - 13, 26))
                body.append(_text(cx, ay + 50, date, 'dt', 'middle'))
                lines = _wrap(short, step - 12, 16)
                body.append(_lines(cx, ay + 76, lines, 'tb', 21, 'middle'))
                bottom = max(bottom, ay + 76 + 21 * (len(lines) - 1))
            height = bottom + pad
        else:
            ay = y + 8
            body.append(f'<path class="ax" d="M{pad + 22:.1f} {ay + 22:.1f}V{ay + 22 + 62 * (len(entries) - 1):.1f}"/>')
            for i, (date, short, glyph, new) in enumerate(entries):
                cy = ay + 22 + 62 * i
                body.append(f'<circle class="stop{" new" if new else ""}" cx="{pad + 22:.1f}" cy="{cy:.1f}" r="22"/>')
                body.append(_glyph(glyph, pad + 10, cy - 12, 24))
                body.append(_text(pad + 58, cy - 4, short if dg._width(short, 16, 'b') < width - pad - 70 else
                                  _wrap(short, width - pad - 76, 16)[0] + '…', 'tb'))
                body.append(_text(pad + 58, cy + 18, date, 'dt'))
            height = ay + 22 + 62 * (len(entries) - 1) + 22 + pad
        out[layout] = _svg(layout, width, height, 'Searchlight reports', desc,
                           f'<rect class="pn" width="{width}" height="{height:.0f}" rx="16"/>' + ''.join(body))
    return out


def _boards(reports: dict) -> list[dict]:
    from .data import leaderboards
    return leaderboards(reports)


def glyph_icon(gid: str) -> str:
    """One glyph, for a line of Markdown."""
    meaning = next(g[2] for g in glyphs.GLYPHS if g[0] == gid)
    return _svg('glyph', 24, 24, meaning.split(':')[0], meaning, _glyph(gid, 0, 0, 24))


# ---------------------------------------------------------------- the design system

def catalogue() -> dict[str, str]:
    """Every glyph with its id, by group, on the soft ground."""
    items = [(g[0], g[1]) for g in glyphs.GLYPHS]
    desc = 'The Searchlight glyphs: ' + ', '.join(i for i, _ in items) + '.'
    out = {}
    for layout, cols, width in (('wide', 6, WIDE), ('tall', 2, TALL)):
        pad = 20 if layout == 'wide' else 12
        cw = (width - 2 * pad) / cols
        body = []
        for i, (gid, _) in enumerate(items):
            x, y = pad + (i % cols) * cw, pad + (i // cols) * 92
            body.append(_glyph(gid, x + cw / 2 - 16, y + 12, 32))
            body.append(_text(x + cw / 2, y + 70, gid, 'gid', 'middle'))
        height = pad + -(-len(items) // cols) * 92 + pad - 8
        out[layout] = _svg(layout, width, height, 'Searchlight glyphs', desc,
                           f'<rect class="pn" width="{width}" height="{height:.0f}" rx="16"/>' + ''.join(body))
    return out


def marks() -> dict[str, str]:
    """The three marks at 64 px, each on the page ground and on the night ground, with its id and name: side by side,
    or one per row on a phone."""
    names = [(g[0], g[1].split(':')[0]) for g in glyphs.LOGOS]
    desc = 'The Searchlight marks: ' + '; '.join(f'{i}, {n}' for i, n in names) + '. Each on the page and on the night ground.'
    out = {}
    for layout in ('wide', 'tall'):
        wide = layout == 'wide'
        cw, ch = (WIDE / len(names), 196) if wide else (TALL, 150)
        body = []
        for i, (gid, name) in enumerate(names):
            x, y = (i * cw, 10) if wide else (0, 10 + i * (ch + 10))
            body.append(f'<rect class="cd" x="{x + 12:.1f}" y="{y:.1f}" width="{cw - 24:.1f}" height="{ch - 10}" rx="12"/>')
            mx = x + cw / 2 - 96 if wide else x + 28
            body.append(_glyph(gid, mx, y + 30, 64))
            body.append(f'<rect class="night" x="{mx + 104:.1f}" y="{y + 14:.1f}" width="96" height="96" rx="12"/>')
            body.append(_glyph(gid, mx + 120, y + 30, 64, ON_NIGHT))
            if wide:
                body.append(_text(x + cw / 2, y + 138, gid, 'gid', 'middle'))
                body.append(_text(x + cw / 2, y + 166, name, 'tb', 'middle'))
            else:
                body.append(_text(x + 28, y + 128, f'{gid} · {name}', 'gid'))
        height = 10 + (ch if wide else len(names) * (ch + 10))
        out[layout] = _svg(layout, cw * len(names) if wide else TALL, height, 'Searchlight marks', desc, ''.join(body))
    return out


# ---------------------------------------------------------------- everything, both themes

def readme_diagrams() -> dict[str, dg.Diagram]:
    """The diagrams as the READMEs draw them: on GitHub, "this site" is a link away, so the pipeline names it."""
    site = dataclasses.replace(dg.PIPELINE, nodes=[dataclasses.replace(n, title='searchlightai.dev') if n.id == 'S'
                                                   else n for n in dg.PIPELINE.nodes])
    return {**dg.DIAGRAMS, 'pipeline': site}


def art(boards: list[dict], analysis: dict, reports: dict, studies: list[dict]) -> dict[str, str]:
    """Every README image, {file name in docs/readme/: standalone SVG}."""
    pieces = {'hero': hero(boards, analysis), 'learn': learn(), 'results': results(boards, reports),
              'setups': setups(boards), 'reports': reports_header(reports, studies), 'glyphs': catalogue(),
              'marks': marks()}
    if studies:
        pieces['study'] = study_results(studies[0])
    for name, diagram in readme_diagrams().items():
        pieces[f'diagram-{name}'] = {'wide': dg._flow_svg(diagram, 'readme'), 'tall': dg._flow_svg(diagram, 'readme-tall')}
    pieces['diagram-grading'] = {'wide': dg._sequence_wide(dg.GRADING, 'readme'),
                                 'tall': dg._sequence_tall(dg.GRADING, 'readme-tall')}
    for gid in ('report', 'study'):
        pieces[f'glyph-{gid}'] = {'wide': glyph_icon(gid)}
    files = {}
    for name, layouts in pieces.items():
        for layout, svg in layouts.items():
            for theme in THEMES:
                suffix = '' if layout == 'wide' else '-tall'
                files[f'{name}{suffix}-{theme}.svg'] = standalone(svg, theme)
    return files


DOCS = ('README.md', 'docs/insight-bench/README.md', 'reports/README.md', 'design/README.md')
CHARTS = ('results', 'study')  # images whose alt text carries their numbers


def alt_texts(files: dict[str, str], root=ROOT) -> dict[str, str]:
    """The READMEs whose chart alt text is out of date, rewritten: a chart's alt text is its own generated description,
    so the numbers a screen reader hears come from the reports too. {path: new text}"""
    out = {}
    for rel in DOCS:
        text = (root / rel).read_text(encoding='utf-8')

        def alt(m):
            desc = re.search(r'<desc id="d">([^<]*)</desc>', files[f'{m.group(2)}-light.svg']).group(1)
            return f'{m.group(1)}{m.group(2)}-light.svg" alt="{desc}"'
        new = re.sub(r'(<img src="[^"]*/)(' + '|'.join(CHARTS) + r')-light\.svg" alt="[^"]*"', alt, text)
        if new != text:
            out[rel] = new
    return out

