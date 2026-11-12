"""The methodology diagrams, drawn here as site-styled SVG: glyph nodes, token colours, labels of 14 px and up.

Each diagram is data (its nodes, edges and groups, the same ones the apparatus has) plus two hand-placed layouts: a
wide one that fits the desktop column and a tall one for phones. CSS shows one or the other; nothing is scaled down
to fit. Colours come from the design tokens through CSS classes, so both themes work. Glyphs come from the sprite every
page already carries.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .data import esc

WIDE, TALL = 960, 360
# Type in each layout, in viewBox units. The wide layout shows from 900 px (a column of at least 860 px), so 16 is at
# least 14.3 px on screen; the tall layout is drawn at 360 and shown 350 to 520 px wide.
TYPE = {'wide': {'t': 17, 'b': 16, 'm': 15, 'e': 15, 'lh': 22, 'gap': 60},
        'tall': {'t': 16, 'b': 15, 'm': 14, 'e': 15, 'lh': 21, 'gap': 52}}
PAD, GLYPH = 14, 26


@dataclass
class Node:
    id: str
    glyph: str
    title: str
    body: list[str] = field(default_factory=list)
    kind: str = ''  # '' a step, 'st' a store, 'q' a decision


@dataclass
class Diagram:
    name: str
    title: str
    nodes: list[Node]
    edges: list[tuple]  # (source, target, label, both, {layout: rail x})
    groups: list[tuple] = field(default_factory=list)  # (label, [node ids])
    wide: list[list[tuple]] = field(default_factory=list)  # rows of (id, x, w[, span])
    tall: list[list[tuple]] = field(default_factory=list)


def _width(text: str, size: float, kind: str) -> float:
    return len(text) * size * {'t': 0.56, 'b': 0.52, 'm': 0.6, 'e': 0.52}[kind]


def wrap(text: str, width: float, size: float, kind: str) -> list[str]:
    words = text.split(' ')
    if kind == 'm':  # a path wraps after a slash
        words = [part for word in words for part in _split_path(word, width, size)]
    lines, line = [], ''
    for word in words:
        trial = f'{line}{word}' if line.endswith(('/', '-')) and not line.endswith(' -') else f'{line} {word}'.strip()
        if line and _width(trial, size, kind) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def _split_path(word: str, width: float, size: float) -> list[str]:
    """A code word too wide for its box breaks after a slash or an inner hyphen, never inside a name."""
    if _width(word, size, 'm') <= width:
        return [word]
    for i in range(len(word) - 1, 2, -1):
        if word[i - 1] in '/-' and _width(word[:i], size, 'm') <= width:
            return [word[:i]] + _split_path(word[i:], width, size)
    return [word]


def _lines(node: Node, w: float, t: dict) -> tuple[bool, list, list]:
    """(stacked, title lines, body lines as (text, kind)). Narrow nodes put the glyph above the title."""
    stacked = w < 200
    title_kind = 'm' if node.title.startswith('`') else 't'
    title_w = w - 2 * PAD - (0 if stacked else GLYPH + 10)
    title = [(x, title_kind) for x in wrap(node.title.lstrip('`'), title_w, t[title_kind], title_kind)]
    body = []
    for text in node.body:
        kind = 'm' if text.startswith('`') else 'b'
        body += [(x, kind) for x in wrap(text.lstrip('`'), w - 2 * PAD, t[kind], kind)]
    return stacked, title, body


def _height(node: Node, w: float, t: dict) -> float:
    stacked, title, body = _lines(node, w, t)
    head = (GLYPH + 8 + len(title) * t['lh']) if stacked else max(GLYPH, len(title) * t['lh'])
    return PAD + head + (8 + len(body) * t['lh'] if body else 0) + PAD - 4


def place(rows: list[list[tuple]], nodes: dict, t: dict) -> dict:
    """Boxes {id: (x, y, w, h)}: rows top to bottom, a node spanning rows centred across them."""
    heights = []
    for row in rows:
        heights.append(max([_height(nodes[c[0]], c[2], t) for c in row if (c[3] if len(c) > 3 else 1) == 1] + [0]))
    tops, y = [], 66.0
    for h in heights:
        tops.append(y)
        y += h + t['gap']
    boxes = {}
    for r, row in enumerate(rows):
        for cell in row:
            nid, x, w = cell[:3]
            span = cell[3] if len(cell) > 3 else 1
            h = _height(nodes[nid], w, t)
            if span == 1:
                peers = len(row) > 1 and len({c[2] for c in row}) == 1  # a row of equal boxes shares one height
                boxes[nid] = (x, tops[r], w, heights[r] if peers else h)
            else:
                band = tops[r + span - 1] + heights[r + span - 1] - tops[r]
                boxes[nid] = (x, tops[r] + (band - h) / 2, w, h)
    return boxes


def _node_svg(node: Node, box: tuple, t: dict) -> str:
    x, y, w, h = box
    stacked, title, body = _lines(node, w, t)
    out = [f'<rect class="nd {node.kind}" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="12"/>',
           f'<use href="#{node.glyph}" x="{x + PAD:.1f}" y="{y + PAD:.1f}" width="{GLYPH}" height="{GLYPH}"/>']
    if stacked:
        tx, ty = x + PAD, y + PAD + GLYPH + 8 + t['lh'] - 6
    else:
        tx, ty = x + PAD + GLYPH + 10, y + PAD + 19
    for i, (text, kind) in enumerate(title):
        out.append(f'<text class="{kind}{" tt" if kind == "m" else ""}" x="{tx:.1f}" y="{ty + i * t["lh"]:.1f}">{esc(text)}</text>')
    by = (ty + len(title) * t['lh'] + 4) if stacked else (y + PAD + max(GLYPH, len(title) * t['lh']) + 8 + t['lh'] - 6)
    for i, (text, kind) in enumerate(body):
        out.append(f'<text class="{kind}" x="{x + PAD:.1f}" y="{by + i * t["lh"]:.1f}">{esc(text)}</text>')
    return ''.join(out)


def _anchor(box: tuple, other: tuple) -> tuple:
    """The midpoint of the side of ``box`` that faces ``other``, and the direction out of it.

    Side by side (no horizontal overlap) leaves from a left or right side; otherwise from the top or bottom."""
    x, y, w, h = box
    ox, oy, ow, oh = other
    cx, cy = x + w / 2, y + h / 2
    if ox >= x + w - 4:
        return (x + w, cy), (1, 0)
    if ox + ow <= x + 4:
        return (x, cy), (-1, 0)
    return ((cx, y + h), (0, 1)) if oy + oh / 2 > cy else ((cx, y), (0, -1))


def _centre(box: tuple) -> tuple:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _route(src: tuple, dst: tuple, rail: float | None) -> tuple[str, tuple]:
    """A path from one box to another, and the point for its label."""
    if rail is not None:
        sx, sy, sw, sh = src
        dx, dy, dw, dh = dst
        start = (rail, sy + sh) if sx <= rail <= sx + sw else ((sx + sw, sy + sh / 2) if rail > sx + sw else (sx, sy + sh / 2))
        end = (rail, dy) if dx <= rail <= dx + dw else ((dx + dw, dy + dh / 2) if rail > dx + dw else (dx, dy + dh / 2))
        pts = [start]
        if start[0] != rail:
            pts.append((rail, start[1]))
        if end[0] != rail:
            pts.append((rail, end[1]))
        pts.append(end)
        d = 'M' + ' L'.join(f'{px:.1f} {py:.1f}' for px, py in pts)
        mid = ((pts[1][0] + pts[-2][0]) / 2, (pts[1][1] + pts[-2][1]) / 2) if len(pts) > 2 else pts[0]
        return d, mid
    (p0, d0), (p1, d1) = _anchor(src, dst), _anchor(dst, src)
    k = max(28.0, (abs(p1[0] - p0[0]) + abs(p1[1] - p0[1])) * 0.35)
    c0 = (p0[0] + d0[0] * k, p0[1] + d0[1] * k)
    c1 = (p1[0] + d1[0] * k, p1[1] + d1[1] * k)
    d = f'M{p0[0]:.1f} {p0[1]:.1f} C{c0[0]:.1f} {c0[1]:.1f} {c1[0]:.1f} {c1[1]:.1f} {p1[0]:.1f} {p1[1]:.1f}'
    mid = (0.125 * p0[0] + 0.375 * c0[0] + 0.375 * c1[0] + 0.125 * p1[0],
           0.125 * p0[1] + 0.375 * c0[1] + 0.375 * c1[1] + 0.125 * p1[1])
    return d, mid


def _label(text: str, at: tuple, t: dict) -> str:
    """An edge label, set just above the edge's midpoint on a paper ground."""
    w = _width(text, t['e'], 'e') + 12
    at = (at[0], at[1] - 14)
    return (f'<rect class="elb" x="{at[0] - w / 2:.1f}" y="{at[1] - t["e"] * 0.85:.1f}" width="{w:.1f}" '
            f'height="{t["e"] * 1.5:.1f}" rx="4"/><text class="el" x="{at[0]:.1f}" y="{at[1] + t["e"] * 0.35:.1f}" '
            f'text-anchor="middle">{esc(text)}</text>')


def _markers(uid: str) -> str:
    return (f'<defs><marker id="{uid}-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
            f'orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker></defs>')


def _flow_svg(diagram: Diagram, layout: str) -> str:
    t = TYPE[layout]
    width = WIDE if layout == 'wide' else TALL
    nodes = {n.id: n for n in diagram.nodes}
    boxes = place(getattr(diagram, layout), nodes, t)
    uid = f'fl-{diagram.name}-{layout}'
    out, groups_text = [_markers(uid)], []
    for label, members in diagram.groups:
        xs = [boxes[m] for m in members]
        x0, x1 = min(b[0] for b in xs) - 10, max(b[0] + b[2] for b in xs) + 10
        lines = wrap(label.upper(), x1 - x0 - 24, 14, 'm')
        y0, y1 = min(b[1] for b in xs) - 18 - 18 * len(lines), max(b[1] + b[3] for b in xs) + 12
        out.append(f'<rect class="grp" x="{x0:.1f}" y="{y0:.1f}" width="{x1 - x0:.1f}" height="{y1 - y0:.1f}" rx="16"/>')
        groups_text.append(''.join(f'<text class="gl" x="{x0 + 12:.1f}" y="{y0 + 20 + 18 * i:.1f}">{esc(line)}</text>'
                                   for i, line in enumerate(lines)))
    labels = []
    for source, target, label, both, rails in diagram.edges:
        d, mid = _route(boxes[source], boxes[target], rails.get(layout) if rails else None)
        start = f' marker-start="url(#{uid}-a)"' if both else ''
        out.append(f'<path class="e" d="{d}" marker-end="url(#{uid}-a)"{start}/>')
        if label:
            labels.append(_label(label, mid, t))
    out += [_node_svg(nodes[nid], box, t) for nid, box in boxes.items()] + labels + groups_text
    height = max(b[1] + b[3] for b in boxes.values()) + 24
    return (f'<svg class="{layout}" viewBox="0 0 {width} {height:.0f}" role="img" aria-labelledby="{uid}-t {uid}-d">'
            f'<title id="{uid}-t">{esc(diagram.title)}</title><desc id="{uid}-d">{esc(describe(diagram))}</desc>'
            + ''.join(out) + '</svg>')


def describe(diagram: Diagram) -> str:
    """The diagram in words, for screen readers: every edge, in order."""
    names = {n.id: ' '.join([n.title.lstrip('`')] + [b.lstrip('`') for b in n.body]) for n in diagram.nodes}
    return '; '.join(f'{names[s]} {"to and from" if both else "to"} {names[d]}' + (f' ({label})' if label else '')
                     for s, d, label, both, _ in diagram.edges) + '.'


# ---------------------------------------------------------------- the sequence diagram: how answers are graded

@dataclass
class Sequence:
    name: str
    title: str
    actors: list[Node]
    messages: list[tuple]  # (source, target, label, reply)
    note: str


def _sequence_wide(seq: Sequence) -> str:
    t = TYPE['wide']
    uid = f'fl-{seq.name}-wide'
    col = WIDE / len(seq.actors)
    w = col - 20
    centres = {a.id: col * i + col / 2 for i, a in enumerate(seq.actors)}
    head = max(_height(a, w, t) for a in seq.actors)
    out = [_markers(uid)]
    for i, a in enumerate(seq.actors):
        out.append(_node_svg(a, (col * i + 10, 20, w, head), t))
    y = 20 + head + 36
    rows = []
    for source, target, label, reply in seq.messages:
        span = abs(centres[target] - centres[source]) - 24
        lines = wrap(label, max(span, col - 30), t['e'], 'e')
        rows.append((source, target, lines, reply, y))
        y += len(lines) * t['lh'] + 34
    note_lines = wrap(seq.note, WIDE - 60, t['b'], 'b')
    note_h = len(note_lines) * t['lh'] + 24
    bottom = y + note_h + 10
    for a in seq.actors:
        out.append(f'<path class="life" d="M{centres[a.id]:.1f} {20 + head:.1f} V{bottom - note_h - 16:.1f}"/>')
    for source, target, lines, reply, ry in rows:
        x0, x1 = centres[source], centres[target]
        direction = 1 if x1 > x0 else -1
        ly = ry + len(lines) * t['lh']
        mid = (x0 + x1) / 2
        for i, line in enumerate(lines):
            out.append(f'<text class="el" x="{mid:.1f}" y="{ry + i * t["lh"] + t["e"]:.1f}" text-anchor="middle">{esc(line)}</text>')
        out.append(f'<path class="e{" r" if reply else ""}" d="M{x0 + direction * 6:.1f} {ly:.1f} H{x1 - direction * 8:.1f}" '
                   f'marker-end="url(#{uid}-a)"/>')
    ny = bottom - note_h
    out.append(f'<rect class="nd q" x="10" y="{ny:.1f}" width="{WIDE - 20}" height="{note_h:.1f}" rx="12"/>')
    for i, line in enumerate(note_lines):
        out.append(f'<text class="b strong" x="{WIDE / 2:.1f}" y="{ny + 12 + t["lh"] * (i + 1) - 4:.1f}" '
                   f'text-anchor="middle">{esc(line)}</text>')
    return (f'<svg class="wide" viewBox="0 0 {WIDE} {bottom + 10:.0f}" role="img" aria-labelledby="{uid}-t {uid}-d">'
            f'<title id="{uid}-t">{esc(seq.title)}</title><desc id="{uid}-d">{esc(_sequence_words(seq))}</desc>'
            + ''.join(out) + '</svg>')


def _sequence_tall(seq: Sequence) -> str:
    """Each message as a step: who to whom, then what passes."""
    t = TYPE['tall']
    uid = f'fl-{seq.name}-tall'
    names = {a.id: a for a in seq.actors}
    out = [_markers(uid)]
    y = 20.0
    for n, (source, target, label, reply) in enumerate(seq.messages, start=1):
        lines = wrap(label, TALL - 2 * PAD - 20, t['b'], 'b')
        h = 14 + 28 * 2 + 8 + len(lines) * t['lh'] + 10
        out.append(f'<rect class="nd" x="10" y="{y:.1f}" width="{TALL - 20}" height="{h:.1f}" rx="12"/>')
        out.append(f'<text class="gl" x="{10 + PAD}" y="{y + 32:.1f}">{n}</text>')
        x = 10 + PAD + 22
        for i, actor in enumerate((names[source], names[target])):
            ay = y + 14 + 28 * i
            if i == 1:
                out.append(f'<path class="e{" r" if reply else ""}" d="M{x + 4:.1f} {ay - 6:.1f} V{ay + 8:.1f} H{x + 22:.1f}" '
                           f'marker-end="url(#{uid}-a)"/>')
            ox = x + (30 if i else 0)
            out.append(f'<use href="#{actor.glyph}" x="{ox:.1f}" y="{ay:.1f}" width="20" height="20"/>')
            out.append(f'<text class="t" x="{ox + 28:.1f}" y="{ay + 16:.1f}">{esc(actor.title)}</text>')
        for i, line in enumerate(lines):
            out.append(f'<text class="b" x="{10 + PAD}" y="{y + 14 + 56 + 8 + i * t["lh"] + 10:.1f}">{esc(line)}</text>')
        y += h + 12
    note_lines = wrap(seq.note, TALL - 2 * PAD - 20, t['b'], 'b')
    note_h = len(note_lines) * t['lh'] + 24
    out.append(f'<rect class="nd q" x="10" y="{y:.1f}" width="{TALL - 20}" height="{note_h:.1f}" rx="12"/>')
    for i, line in enumerate(note_lines):
        out.append(f'<text class="b strong" x="{10 + PAD}" y="{y + 12 + t["lh"] * (i + 1) - 4:.1f}">{esc(line)}</text>')
    y += note_h + 20
    return (f'<svg class="tall" viewBox="0 0 {TALL} {y:.0f}" role="img" aria-labelledby="{uid}-t {uid}-d">'
            f'<title id="{uid}-t">{esc(seq.title)}</title><desc id="{uid}-d">{esc(_sequence_words(seq))}</desc>'
            + ''.join(out) + '</svg>')


def _sequence_words(seq: Sequence) -> str:
    names = {a.id: ' '.join([a.title] + a.body) for a in seq.actors}
    return '; '.join(f'{names[s]} to {names[d]}: {label}' for s, d, label, _ in seq.messages) + f'. {seq.note}'


# ---------------------------------------------------------------- the five diagrams

L, R, F = (22, 148), (190, 148), (12, 336)  # tall columns: left, right, full width

PIPELINE = Diagram(
    'pipeline', 'From task to report',
    [Node('T1', 'benchmark', 'Production tasks', ['web search bakeoff']),
     Node('T2', 'brief', 'Gap briefs', ['post-cutoff events']),
     Node('W', 'sealed', 'Fresh scratch workspace'),
     Node('H', 'agent', 'Agent', ['Claude Code or Codex']),
     Node('M', 'tokens', 'Metering proxy'),
     Node('V', 'provider', 'Search provider MCP server', ['pinned version'], 'st'),
     Node('B', 'report', 'Evidence bundle', ['transcript, final answer, setup audit, metrics'], 'st'),
     Node('D', 'verified', 'Deterministic validators'),
     Node('J', 'judge', 'Blinded judges', ['vs captured sources']),
     Node('R', 'results-list', '`reports/*/summary.json', [], 'st'),
     Node('S', 'beam', 'This site')],
    [('T1', 'W', '', False, None), ('T2', 'W', '', False, None), ('W', 'H', '', False, None),
     ('H', 'M', 'tool calls', True, None), ('M', 'V', '', True, None), ('H', 'B', '', False, None),
     ('B', 'D', '', False, None), ('B', 'J', '', False, None), ('D', 'R', '', False, None), ('J', 'R', '', False, None),
     ('R', 'S', '', False, None)],
    [('Task catalogs', ['T1', 'T2']), ('One test run = task × search setup × repetition', ['W', 'H', 'M', 'V']),
     ('Grading', ['D', 'J'])],
    wide=[[('T1', 12, 150), ('W', 206, 150, 2), ('H', 392, 160, 2), ('M', 640, 130, 2), ('V', 808, 140, 2)],
          [('T2', 12, 150)],
          [('B', 392, 160, 2), ('D', 600, 160), ('R', 808, 140, 2)],
          [('J', 600, 160)],
          [('S', 808, 140)]],
    tall=[[('T1', *L), ('T2', *R)], [('W', *F)], [('H', *F)], [('M', *L), ('V', *R)], [('B', *F)],
          [('D', *L), ('J', *R)], [('R', *F)], [('S', *F)]],
)

ISOLATION = Diagram(
    'isolation', 'What each setup can touch',
    [Node('A', 'setup', 'Search setup contract'),
     Node('CC', 'agent', 'Claude Code spawn', ['`--strict-mcp-config', "`--tools: the setup's built-ins",
                                               '`--allowedTools: the same set']),
     Node('CX', 'agent', 'Codex spawn', ['`exec --sandbox read-only', '`--disable shell_tool, browser_use, apps, plugins, ...']),
     Node('P1', 'provider', 'Search provider', ['one provider MCP server + ToolSearch + Read']),
     Node('N1', 'built-in', 'Built-in search', ['WebSearch + WebFetch + Read']),
     Node('F1', 'no-search', 'No search, floor, ceiling', ['no tools']),
     Node('P2', 'provider', 'Search provider', ['one provider MCP server']),
     Node('N2', 'built-in', 'Built-in search', ['`codex --search']),
     Node('F2', 'no-search', 'No search, floor, ceiling', ['no tools']),
     Node('AU', 'strict-credit', 'Setup audit', ['every observed tool call checked against the contract'])],
    [('A', 'CC', '', False, None), ('A', 'CX', '', False, None),
     ('CC', 'P1', '', False, None), ('CC', 'N1', '', False, {'tall': 10}), ('CC', 'F1', '', False, {'tall': 10}),
     ('CX', 'P2', '', False, None), ('CX', 'N2', '', False, {'tall': 350}), ('CX', 'F2', '', False, {'tall': 350}),
     ('P1', 'AU', '', False, {'tall': 180}), ('N1', 'AU', '', False, {'tall': 180}), ('F1', 'AU', '', False, {'tall': 180}),
     ('P2', 'AU', '', False, {'tall': 180}), ('N2', 'AU', '', False, {'tall': 180}), ('F2', 'AU', '', False, {'tall': 180})],
    [],
    wide=[[('A', 350, 260)], [('CC', 90, 330), ('CX', 540, 330)],
          [('P1', 0, 150), ('N1', 162, 150), ('F1', 324, 150), ('P2', 486, 150), ('N2', 648, 150), ('F2', 810, 150)],
          [('AU', 300, 360)]],
    tall=[[('A', *F)], [('CC', *L), ('CX', *R)], [('P1', *L), ('P2', *R)], [('N1', *L), ('N2', *R)],
          [('F1', *L), ('F2', *R)], [('AU', *F)]],
)

CALIBRATION = Diagram(
    'calibration', 'Which tasks count',
    [Node('T', 'brief', 'Candidate brief', ['event after 2026-01-01']),
     Node('FL', 'no-search', 'Floor: no search', ['5 runs']),
     Node('CE', 'source', 'Ceiling: answer excerpt in the prompt', ['5 runs']),
     Node('Q', 'filter', 'Floor passes ≤ 1 and ceiling passes ≥ 4?', [], 'q'),
     Node('AD', 'verified', 'Admitted', ['for this agent and model']),
     Node('RJ', 'unsupported', 'Rejected'),
     Node('BAT', 'repeat', 'Battery', ['every search setup × 3']),
     Node('GC', 'gap-closed', 'Gap closure', ['(setup − floor) ÷ (ceiling − floor)'])],
    [('T', 'FL', '', False, None), ('T', 'CE', '', False, None), ('FL', 'Q', '', False, None), ('CE', 'Q', '', False, None),
     ('Q', 'AD', 'yes', False, None), ('Q', 'RJ', 'no', False, None), ('AD', 'BAT', '', False, None),
     ('BAT', 'GC', '', False, None)],
    [],
    wide=[[('T', 0, 180, 2), ('FL', 230, 210), ('Q', 490, 220, 2), ('RJ', 760, 200)],
          [('CE', 230, 210), ('AD', 760, 200)],
          [('GC', 490, 220), ('BAT', 760, 200)]],
    tall=[[('T', *F)], [('FL', *L), ('CE', *R)], [('Q', *F)], [('AD', *L), ('RJ', *R)], [('BAT', *L)], [('GC', *F)]],
)

DIRECT = Diagram(
    'direct', 'The search API head-to-head',
    [Node('Q', 'pre-registered', 'Pre-registered question sets', ['from one knowledge base']),
     Node('E', 'provider', 'Exa'), Node('T', 'provider', 'Tavily'),
     Node('P', 'provider', 'Parallel'), Node('F', 'provider', 'Firecrawl'),
     Node('PO', 'results-list', 'Results pooled', ['deduplicated, shuffled'], 'st'),
     Node('J', 'blind', 'Blind model judges', ['verify each claim on its page']),
     Node('K', 'filter', 'Already in the knowledge base?', [], 'q'),
     Node('A', 'strict-credit', 'Attribution rejoined', ["credit only where the provider's own page states the claim"]),
     Node('R', 'results-list', '`reports/*/summary.json', [], 'st')],
    [('Q', 'E', '', False, {'tall': 10}), ('Q', 'T', '', False, {'tall': 350}), ('Q', 'P', '', False, {'tall': 10}),
     ('Q', 'F', '', False, {'tall': 350}),
     ('E', 'PO', '', False, {'tall': 180}), ('T', 'PO', '', False, {'tall': 180}), ('P', 'PO', '', False, {'tall': 180}),
     ('F', 'PO', '', False, {'tall': 180}),
     ('PO', 'J', '', False, None), ('J', 'K', '', False, None), ('K', 'A', '', False, None), ('A', 'R', '', False, None)],
    [],
    wide=[[('Q', 0, 200, 4), ('E', 250, 150), ('PO', 450, 180, 4), ('J', 680, 280)],
          [('T', 250, 150), ('K', 680, 280)],
          [('P', 250, 150), ('A', 680, 280)],
          [('F', 250, 150), ('R', 680, 280)]],
    tall=[[('Q', *F)], [('E', *L), ('T', *R)], [('P', *L), ('F', *R)], [('PO', *F)], [('J', *F)], [('K', *F)],
          [('A', *F)], [('R', *F)]],
)

GRADING = Sequence(
    'grading', 'How answers are graded',
    [Node('A', 'brief', 'Agent answer'), Node('V', 'filter', 'Schema validator'), Node('S', 'source', 'Source capture'),
     Node('J1', 'judge', 'Judge 1', ['Claude Code']), Node('J2', 'judge', 'Judge 2', ['Codex, gap bench'])],
    [('A', 'V', 'JSON deliverable (brief, claims, citations)', False),
     ('V', 'A', 'reject malformed answers', True),
     ('A', 'S', 'cited URLs', False),
     ('S', 'J1', 'readable source text + rubric (blinded to the search setup)', False),
     ('S', 'J2', 'same inputs', False),
     ('J1', 'A', 'key-fact recall, unsupported claims, decision', True),
     ('J2', 'A', 'independent labels, agreement recorded', True)],
    'Pass = right decision AND recall ≥ 0.7 AND unsupported ≤ 0.25',
)

DIAGRAMS = {d.name: d for d in (PIPELINE, ISOLATION, CALIBRATION, DIRECT)}


def figure(name: str) -> str:
    """One diagram, in its wide and its tall layout; CSS shows the one that fits."""
    if name == GRADING.name:
        svgs = _sequence_wide(GRADING) + _sequence_tall(GRADING)
    else:
        svgs = _flow_svg(DIAGRAMS[name], 'wide') + _flow_svg(DIAGRAMS[name], 'tall')
    return f'<figure class="flow" id="flow-{name}">{svgs}</figure>'
