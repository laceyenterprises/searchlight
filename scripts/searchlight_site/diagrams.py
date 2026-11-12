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
# least 14.3 px on screen; the tall layout is drawn at 360 and shown 350 to 520 px wide. 'g' is the group label.
# The README layouts are the same rows with larger type, for GitHub's columns: 'readme' is shown in an 830 px column
# (17 is 14.7 px), 'readme-tall' 326 to 358 px wide (16 is at least 14.5 px).
TYPE = {'wide': {'t': 17, 'b': 16, 'm': 15, 'e': 15, 'g': 14, 'lh': 22, 'gap': 60, 'layout': 'wide', 'width': WIDE},
        'tall': {'t': 16, 'b': 15, 'm': 14, 'e': 15, 'g': 14, 'lh': 21, 'gap': 52, 'layout': 'tall', 'width': TALL},
        'readme': {'t': 18, 'b': 17, 'm': 17, 'e': 17, 'g': 17, 'lh': 24, 'gap': 60, 'layout': 'wide', 'width': WIDE,
                   'rows': 'wide'},
        'readme-tall': {'t': 16, 'b': 16, 'm': 16, 'e': 16, 'g': 16, 'lh': 22, 'gap': 52, 'layout': 'tall',
                        'width': TALL, 'rows': 'tall'}}
GUTTER = 12  # every card keeps this far from the drawing's edge, so no border is clipped


def rows_for(diagram, layout: str) -> list[list[tuple]]:
    """A layout's rows: a README layout draws the rows of the site layout it is based on."""
    return getattr(diagram, TYPE[layout].get('rows', layout))


def type_css(layout: str) -> str:
    """Type sizes for a README layout's drawing (class rd), where they differ from the site's."""
    t = TYPE[layout]
    return (f'.rd .t{{font-size:{t["t"]}px}}.rd .b{{font-size:{t["b"]}px}}.rd .m{{font-size:{t["m"]}px}}'
            f'.rd .el{{font-size:{t["e"]}px}}.rd .gl{{font-size:{t["g"]}px}}')
PAD, GLYPH = 14, 26


@dataclass
class Node:
    id: str
    glyph: str
    title: str
    body: list[str] = field(default_factory=list)
    kind: str = ''  # '' a step, 'st' a store, 'q' a decision
    tall: list[str] | None = None  # a shorter body for the phone layout (the full one stays in the description)


@dataclass
class Diagram:
    name: str
    title: str
    nodes: list[Node]
    edges: list[tuple]  # (source, target, label, both, {layout: rail x})
    groups: list[tuple] = field(default_factory=list)  # (label, [node ids])
    wide: list[list[tuple]] = field(default_factory=list)  # rows of (id, x, w[, span])
    tall: list[list[tuple]] = field(default_factory=list)
    flow: str = 'LR'  # the wide layout's direction; the tall layout always runs top to bottom


def _width(text: str, size: float, kind: str) -> float:
    return len(text) * size * {'t': 0.56, 'b': 0.56, 'm': 0.6, 'e': 0.52}[kind]


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
    for text in node.tall if t['layout'] == 'tall' and node.tall is not None else node.body:
        kind = 'm' if text.startswith('`') else 'b'
        body += [(x, kind) for x in wrap(text.lstrip('`'), w - 2 * PAD, t[kind], kind)]
    return stacked, title, body


def _height(node: Node, w: float, t: dict) -> float:
    stacked, title, body = _lines(node, w, t)
    head = (GLYPH + 8 + len(title) * t['lh']) if stacked else max(GLYPH, len(title) * t['lh'])
    return PAD + head + (8 + len(body) * t['lh'] if body else 0) + PAD - 4


GROUP_PAD, GROUP_GAP, LABEL_LH = 12, 24, 18


def group_label(group: tuple, layout: str) -> str:
    """A group's label in this layout: a tall layout can carry a shorter one (the full one goes in the caption)."""
    return (group[2].get(layout) if len(group) > 2 else None) or group[0]


def _cells(rows: list[list[tuple]]) -> dict:
    return {c[0]: (r, c[1], c[2], c[3] if len(c) > 3 else 1) for r, row in enumerate(rows) for c in row}


def _bands(rows: list[list[tuple]], groups: list[tuple], layout: str) -> tuple[dict, dict, dict]:
    """For each group: its label lines; and, per row, the room a group's label band needs above it and the room a
    group's bottom padding needs below it."""
    cells = _cells(rows)
    lines, before, after = {}, {}, {}
    for g in groups:
        members = [cells[m] for m in g[1]]
        x0, x1 = min(c[1] for c in members) - 10, max(c[1] + c[2] for c in members) + 10
        size = TYPE[layout]['g']
        lines[g[0]] = wrap(group_label(g, TYPE[layout]['layout']).upper(), x1 - x0 - 24, size, 'm')
        first, last = min(c[0] for c in members), max(c[0] + c[3] - 1 for c in members)
        band = 14 + (size + 4) * len(lines[g[0]]) + 10
        before[first] = max(before.get(first, 0), band)
        after[last] = max(after.get(last, 0), GROUP_PAD)
    return lines, before, after


def place(rows: list[list[tuple]], nodes: dict, t: dict, groups: list[tuple] = (), layout: str = 'wide') -> dict:
    """Boxes {id: (x, y, w, h)}: rows top to bottom, a node spanning rows centred across them.

    A row that starts a group leaves room above it for the group's label band; a row that ends one leaves room for
    its bottom padding; two groups never come closer than GROUP_GAP."""
    _, before, after = _bands(rows, groups, layout)
    heights = []
    for row in rows:
        heights.append(max([_height(nodes[c[0]], c[2], t) for c in row if (c[3] if len(c) > 3 else 1) == 1] + [0]))
    tops, y = [], 24.0 + before.get(0, 0)
    for r, h in enumerate(heights):
        tops.append(y)
        need = after.get(r, 0) + before.get(r + 1, 0)
        y += h + max(t['gap'], need + (GROUP_GAP if need else 0))
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


def _anchor(box: tuple, other: tuple, flow: str) -> tuple:
    """The midpoint of the side of ``box`` that faces ``other``, and the direction out of it.

    In a left-to-right flow, boxes that do not overlap horizontally connect side to side; in a top-to-bottom flow,
    only boxes that share some height do. Everything else connects top to bottom."""
    x, y, w, h = box
    ox, oy, ow, oh = other
    cx, cy = x + w / 2, y + h / 2
    sideways = True if flow == 'LR' else (oy < y + h and oy + oh > y)
    if sideways and ox >= x + w - 4:
        return (x + w, cy), (1, 0)
    if sideways and ox + ow <= x + 4:
        return (x, cy), (-1, 0)
    return ((cx, y + h), (0, 1)) if oy + oh / 2 > cy else ((cx, y), (0, -1))


def _centre(box: tuple) -> tuple:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _bezier(p0, c0, c1, p1, n: int = 24) -> list[tuple]:
    out = []
    for i in range(n + 1):
        u = i / n
        a, b, c, d = (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u * u * (1 - u), u ** 3
        out.append((a * p0[0] + b * c0[0] + c * c1[0] + d * p1[0], a * p0[1] + b * c0[1] + c * c1[1] + d * p1[1]))
    return out


def _route(src: tuple, dst: tuple, rail: float | None, entry: float | None = None,
           flow: str = 'TB') -> tuple[str, tuple, list]:
    """A path from one box to another, the point for its label, and the points it passes through.

    ``rail``: run along a vertical line at that x. ``entry``: the target sits in a group below; drop to just above the
    group, cross to the target's centre, and enter through the group's top edge, clear of its label."""
    if entry is not None:
        sx, sy, sw, sh = src
        dx, dy, dw, dh = dst
        pts = [(sx + sw / 2, sy + sh), (sx + sw / 2, entry), (dx + dw / 2, entry), (dx + dw / 2, dy)]
        pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        d = 'M' + ' L'.join(f'{px:.1f} {py:.1f}' for px, py in pts)
        return d, ((pts[1][0] + pts[-2][0]) / 2, entry), pts
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
        return d, mid, pts
    (p0, d0), (p1, d1) = _anchor(src, dst, flow), _anchor(dst, src, flow)
    k = max(28.0, (abs(p1[0] - p0[0]) + abs(p1[1] - p0[1])) * 0.35)
    c0 = (p0[0] + d0[0] * k, p0[1] + d0[1] * k)
    c1 = (p1[0] + d1[0] * k, p1[1] + d1[1] * k)
    d = f'M{p0[0]:.1f} {p0[1]:.1f} C{c0[0]:.1f} {c0[1]:.1f} {c1[0]:.1f} {c1[1]:.1f} {p1[0]:.1f} {p1[1]:.1f}'
    mid = (0.125 * p0[0] + 0.375 * c0[0] + 0.375 * c1[0] + 0.125 * p1[0],
           0.125 * p0[1] + 0.375 * c0[1] + 0.375 * c1[1] + 0.125 * p1[1])
    return d, mid, _bezier(p0, c0, c1, p1)


def label_box(text: str, at: tuple, t: dict, upright: bool = False) -> tuple:
    """Where an edge label sits: on a slanting or level edge's midpoint, or just right of an upright one."""
    w, h = _width(text, t['e'], 'e') + 12, t['e'] * 1.5
    if upright:
        return (at[0] + 8, at[1] - h / 2, w, h)
    return (at[0] - w / 2, at[1] - h / 2, w, h)


def _label(text: str, box: tuple, t: dict) -> str:
    """An edge label on a paper ground, so the line it names never runs through its words."""
    x, y, w, h = box
    return (f'<rect class="elb" x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="4"/>'
            f'<text class="el" x="{x + w / 2:.1f}" y="{y + t["e"] * 1.2:.1f}" text-anchor="middle">{esc(text)}</text>')


def _markers(uid: str) -> str:
    return (f'<defs><marker id="{uid}-a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
            f'orient="auto-start-reverse"><path class="ah" d="M0 0L10 5L0 10z"/></marker></defs>')


def _upright(points: list) -> bool:
    return abs(points[-1][1] - points[0][1]) > abs(points[-1][0] - points[0][0])


def geometry(diagram: Diagram, layout: str) -> dict:
    """Everything the drawing places: node boxes, group rectangles and label boxes, and each edge's points.

    The SVG is drawn from this, and the tests check it: no group label is crossed by an edge, groups keep their
    distance, and no edge runs through a card it does not start or end at."""
    t = TYPE[layout]
    rows = rows_for(diagram, layout)
    nodes = {n.id: n for n in diagram.nodes}
    boxes = place(rows, nodes, t, diagram.groups, layout)
    lines, _, _ = _bands(rows, diagram.groups, layout)
    groups, member_of = [], {}
    for g in diagram.groups:
        xs = [boxes[m] for m in g[1]]
        x0, x1 = min(b[0] for b in xs) - 10, max(b[0] + b[2] for b in xs) + 10
        band = 14 + (t['g'] + 4) * len(lines[g[0]]) + 10
        y0, y1 = min(b[1] for b in xs) - band, max(b[1] + b[3] for b in xs) + GROUP_PAD
        width = max(_width(line, t['g'], 'm') + 0.06 * t['g'] * len(line) for line in lines[g[0]])  # .06em tracking
        groups.append({'rect': (x0, y0, x1 - x0, y1 - y0), 'lines': lines[g[0]],
                       'label': (x0 + 8, y0 + 4, width + 8, band - 8)})
        for m in g[1]:
            member_of[m] = len(groups) - 1
    edges = []
    for source, target, label, both, rails in diagram.edges:
        entry = None
        g = member_of.get(target)
        if g is not None and member_of.get(source) != g and boxes[source][1] + boxes[source][3] < groups[g]['rect'][1]:
            entry = groups[g]['rect'][1] - 14
        flow = diagram.flow if t['layout'] == 'wide' else 'TB'
        rail = rails.get(t.get('rows', layout)) if rails else None
        d, mid, pts = _route(boxes[source], boxes[target], rail, entry, flow)
        edges.append({'source': source, 'target': target, 'd': d, 'mid': mid, 'points': pts, 'both': both,
                      'label': label, 'label_box': label_box(label, mid, t, _upright(pts)) if label else None})
    return {'boxes': boxes, 'groups': groups, 'edges': edges, 't': t,
            'height': max(b[1] + b[3] for b in boxes.values()) + 24}


def _flow_svg(diagram: Diagram, layout: str) -> str:
    geo = geometry(diagram, layout)
    t = geo['t']
    width = t['width']
    nodes = {n.id: n for n in diagram.nodes}
    uid = f'fl-{diagram.name}-{layout}'
    out, labels, group_text = [_markers(uid)], [], []
    for g in geo['groups']:
        x0, y0, w, h = g['rect']
        out.append(f'<rect class="grp" x="{x0:.1f}" y="{y0:.1f}" width="{w:.1f}" height="{h:.1f}" rx="16"/>')
        group_text.append(''.join(f'<text class="gl" x="{x0 + 12:.1f}" y="{y0 + 14 + t["g"] - 1 + (t["g"] + 4) * i:.1f}">'
                                  f'{esc(line)}</text>' for i, line in enumerate(g['lines'])))
    for e in geo['edges']:
        start = f' marker-start="url(#{uid}-a)"' if e['both'] else ''
        out.append(f'<path class="e" d="{e["d"]}" marker-end="url(#{uid}-a)"{start}/>')
        if e['label']:
            labels.append(_label(e['label'], e['label_box'], t))
    out += [_node_svg(nodes[nid], box, t) for nid, box in geo['boxes'].items()] + labels + group_text
    return (f'<svg class="{_cls(layout)}" viewBox="0 0 {width} {geo["height"]:.0f}" role="img" '
            f'aria-labelledby="{uid}-t {uid}-d"><title id="{uid}-t">{esc(diagram.title)}</title>'
            f'<desc id="{uid}-d">{esc(describe(diagram))}</desc>' + ''.join(out) + '</svg>')


def _cls(layout: str) -> str:
    return TYPE[layout]['layout'] + (' rd' if layout.startswith('readme') else '')


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


def _sequence_wide(seq: Sequence, layout: str = 'wide') -> str:
    t = TYPE[layout]
    W = t['width']
    uid = f'fl-{seq.name}-{layout}'
    col = (W - 2 * GUTTER + 20) / len(seq.actors)
    w = col - 20
    centres = {a.id: GUTTER - 10 + col * i + col / 2 for i, a in enumerate(seq.actors)}
    head = max(_height(a, w, t) for a in seq.actors)
    out = [_markers(uid)]
    for i, a in enumerate(seq.actors):
        out.append(_node_svg(a, (GUTTER + col * i, 20, w, head), t))
    y = 20 + head + 36
    rows = []
    for source, target, label, reply in seq.messages:
        span = abs(centres[target] - centres[source]) - 24
        lines = wrap(label, max(span, col - 30), t['e'], 'e')
        rows.append((source, target, lines, reply, y))
        y += len(lines) * t['lh'] + 34
    note_lines = wrap(seq.note, W - 60, t['b'], 'b')
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
    out.append(f'<rect class="nd q" x="{GUTTER}" y="{ny:.1f}" width="{W - 2 * GUTTER}" height="{note_h:.1f}" rx="12"/>')
    for i, line in enumerate(note_lines):
        out.append(f'<text class="b strong" x="{W / 2:.1f}" y="{ny + 12 + t["lh"] * (i + 1) - 4:.1f}" '
                   f'text-anchor="middle">{esc(line)}</text>')
    return (f'<svg class="{_cls(layout)}" viewBox="0 0 {W} {bottom + 10:.0f}" role="img" aria-labelledby="{uid}-t {uid}-d">'
            f'<title id="{uid}-t">{esc(seq.title)}</title><desc id="{uid}-d">{esc(_sequence_words(seq))}</desc>'
            + ''.join(out) + '</svg>')


def steps(seq: Sequence) -> list[tuple[list[str], list[str], list[str], bool]]:
    """The messages folded into steps for the tall layout: a reply joins its request, and messages that leave one actor
    for several (or return from several to one) together are one step. (sources, targets, labels, all replies)."""
    out = []
    for source, target, label, reply in seq.messages:
        if out:
            srcs, dsts, labels, replies = out[-1]
            back = reply and not replies and srcs == [target] and dsts == [source]
            fan = not reply and not replies and srcs == [source] and target not in dsts and len(labels) == len(dsts)
            gather = reply and replies and dsts == [target] and source not in srcs
            if back or fan or gather:
                out[-1] = (srcs + ([source] if gather else []), dsts + ([target] if fan else []), labels + [label],
                           replies and reply)
                continue
        out.append(([source], [target], [label], reply))
    return out


def _sequence_tall(seq: Sequence, layout: str = 'tall') -> str:
    """Each step as a card: who to whom, then what passes. A reply folds into its request, a fan-out into one step."""
    t = TYPE[layout]
    W = t['width']
    uid = f'fl-{seq.name}-{layout}'
    names = {a.id: a for a in seq.actors}
    out = [_markers(uid)]
    y = 20.0
    for n, (srcs, dsts, labels, reply) in enumerate(steps(seq), start=1):
        lines = wrap('; '.join(labels), W - 2 * GUTTER - 2 * PAD, t['b'], 'b')
        h = 14 + 28 * 2 + 8 + len(lines) * t['lh'] + 10
        out.append(f'<rect class="nd" x="{GUTTER}" y="{y:.1f}" width="{W - 2 * GUTTER}" height="{h:.1f}" rx="12"/>')
        out.append(f'<text class="gl" x="{GUTTER + PAD}" y="{y + 32:.1f}">{n}</text>')
        x = GUTTER + PAD + 22
        for i, group in enumerate((srcs, dsts)):
            ay = y + 14 + 28 * i
            if i == 1:
                out.append(f'<path class="e{" r" if reply else ""}" d="M{x + 4:.1f} {ay - 6:.1f} V{ay + 8:.1f} H{x + 22:.1f}" '
                           f'marker-end="url(#{uid}-a)"/>')
            ox = x + (30 if i else 0)
            actor = names[group[0]]
            title = ' and '.join(names[a].title for a in group)
            out.append(f'<use href="#{actor.glyph}" x="{ox:.1f}" y="{ay:.1f}" width="20" height="20"/>')
            out.append(f'<text class="t" x="{ox + 28:.1f}" y="{ay + 16:.1f}">{esc(title)}</text>')
        for i, line in enumerate(lines):
            out.append(f'<text class="b" x="{GUTTER + PAD}" y="{y + 14 + 56 + 8 + i * t["lh"] + 10:.1f}">{esc(line)}</text>')
        y += h + 12
    note_lines = wrap(seq.note, W - 2 * GUTTER - 2 * PAD, t['b'], 'b')
    note_h = len(note_lines) * t['lh'] + 24
    out.append(f'<rect class="nd q" x="{GUTTER}" y="{y:.1f}" width="{W - 2 * GUTTER}" height="{note_h:.1f}" rx="12"/>')
    for i, line in enumerate(note_lines):
        out.append(f'<text class="b strong" x="{GUTTER + PAD}" y="{y + 12 + t["lh"] * (i + 1) - 4:.1f}">{esc(line)}</text>')
    y += note_h + 20
    return (f'<svg class="{_cls(layout)}" viewBox="0 0 {W} {y:.0f}" role="img" aria-labelledby="{uid}-t {uid}-d">'
            f'<title id="{uid}-t">{esc(seq.title)}</title><desc id="{uid}-d">{esc(_sequence_words(seq))}</desc>'
            + ''.join(out) + '</svg>')


def _sequence_words(seq: Sequence) -> str:
    names = {a.id: ' '.join([a.title] + a.body) for a in seq.actors}
    return '; '.join(f'{names[s]} to {names[d]}: {label}' for s, d, label, _ in seq.messages) + f'. {seq.note}'


# ---------------------------------------------------------------- the diagrams

L, R, F = (22, 148), (190, 148), (12, 336)  # tall columns: left, right, full width
C = (44, 300)  # a single column with room for a rail on its left

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
     ('H', 'M', 'tool calls', True, None), ('M', 'V', '', True, None), ('H', 'B', '', False, {'tall': 20}),
     ('B', 'D', '', False, None), ('B', 'J', '', False, None), ('D', 'R', '', False, None), ('J', 'R', '', False, None),
     ('R', 'S', '', False, None)],
    [('Task catalogs', ['T1', 'T2']),
     ('One test run = task × search setup × repetition', ['W', 'H', 'M', 'V'], {'tall': 'One test run'}),
     ('Grading', ['D', 'J'])],
    wide=[[('T1', 12, 150), ('W', 206, 150, 2), ('H', 392, 150, 2), ('M', 650, 130, 2), ('V', 812, 136, 2)],
          [('T2', 12, 150)],
          [('B', 392, 150, 2), ('D', 600, 160), ('R', 790, 158, 2)],
          [('J', 600, 160)],
          [('S', 700, 248)]],
    tall=[[('T1', 40, 146), ('T2', 196, 146)], [('W', *C)], [('H', *C)], [('M', *C)], [('V', *C)], [('B', *C)],
          [('D', 48, 146), ('J', 200, 146)], [('R', *C)], [('S', *C)]],
)

ISOLATION = Diagram(
    'isolation', 'What each setup can touch',
    [Node('A', 'setup', 'Search setup contract'),
     Node('CC', 'agent', 'Claude Code spawn', ['`--strict-mcp-config', "`--tools: the setup's built-ins",
                                               '`--allowedTools: the same set'], tall=["only the setup's tools"]),
     Node('CX', 'agent', 'Codex spawn', ['`exec --sandbox read-only', '`--disable shell_tool, browser_use, apps, plugins, ...'],
          tall=['read-only, no shell']),
     Node('P1', 'provider', 'Search provider', ['one provider MCP server + ToolSearch + Read']),
     Node('N1', 'built-in', 'Built-in search', ['WebSearch + WebFetch + Read'], tall=['web search, fetch, read']),
     Node('F1', 'no-search', 'No search, floor, ceiling', ['no tools']),
     Node('P2', 'provider', 'Search provider', ['one provider MCP server']),
     Node('N2', 'built-in', 'Built-in search', ['`codex --search'], tall=['`--search']),
     Node('F2', 'no-search', 'No search, floor, ceiling', ['no tools']),
     Node('AU', 'strict-credit', 'Setup audit', ['every observed tool call checked against the contract'])],
    [('A', 'CC', '', False, None), ('A', 'CX', '', False, None),
     ('CC', 'P1', '', False, None), ('CC', 'N1', '', False, {'tall': 10}), ('CC', 'F1', '', False, {'tall': 10}),
     ('CX', 'P2', '', False, None), ('CX', 'N2', '', False, {'tall': 350}), ('CX', 'F2', '', False, {'tall': 350}),
     ('P1', 'AU', '', False, {'tall': 180}), ('N1', 'AU', '', False, {'tall': 180}), ('F1', 'AU', '', False, {'tall': 180}),
     ('P2', 'AU', '', False, {'tall': 180}), ('N2', 'AU', '', False, {'tall': 180}), ('F2', 'AU', '', False, {'tall': 180})],
    [],
    wide=[[('A', 350, 260)], [('CC', 90, 330), ('CX', 540, 330)],
          [('P1', 12, 146), ('N1', 169, 146), ('F1', 326, 146), ('P2', 483, 146), ('N2', 640, 146), ('F2', 797, 146)],
          [('AU', 300, 360)]],
    flow='TB',
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
    wide=[[('T', 12, 170, 2), ('FL', 226, 210), ('Q', 476, 196, 2), ('RJ', 772, 176)],
          [('CE', 226, 210), ('AD', 772, 176)],
          [('GC', 476, 196), ('BAT', 772, 176)]],
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
    wide=[[('Q', 12, 190, 4), ('E', 250, 150), ('PO', 450, 180, 4), ('J', 680, 268)],
          [('T', 250, 150), ('K', 680, 268)],
          [('P', 250, 150), ('A', 680, 268)],
          [('F', 250, 150), ('R', 680, 268)]],
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

INSIGHT = Diagram(
    'insight', 'How an insight study works',
    [Node('S', 'pre-registered', 'Question set', ['`catalogs/insight/*.json']),
     Node('P', 'setup', 'Frozen request profiles', ['`config/insight/profiles/'], tall=['one per service']),
     Node('W', 'query', 'Shared query-writer', ['once per question, stored']),
     Node('R', 'run', '`sew insight run', ['every question × every service, same day; spend ledger, resumable'],
          tall=['every question × every service, same day']),
     Node('C', 'results-list', 'Cells', ['request, raw response, cost, latency'], 'st'),
     Node('E', 'filter', 'Extract', ['one fixed model: atomic claims, each with its source URL'],
          tall=['atomic claims with their sources']),
     Node('K', 'overlap', 'Cluster', ['per question: lexical prefilter, then model equivalence'],
          tall=['the same finding, pooled per question']),
     Node('V', 'verified', 'Verify', ['each claim on the page it cites']),
     Node('J', 'blind', 'Value 0–3', ['verified clusters, blind, in a seeded shuffle']),
     Node('T', 'report', '`sew insight report', ['unique verified insight per service'], 'st')],
    [('S', 'W', '', False, None), ('P', 'W', '', False, None), ('W', 'R', '', False, None), ('R', 'C', '', False, None),
     ('C', 'E', '', False, None), ('E', 'K', '', False, None), ('K', 'V', '', False, None), ('V', 'J', '', False, None),
     ('J', 'T', '', False, None)],
    [('Grading: one extractor, a blind judge', ['E', 'K', 'V', 'J'], {'tall': 'Grading'})],
    wide=[[('S', 12, 190), ('W', 252, 196, 2), ('R', 498, 214, 2), ('C', 762, 186, 2)],
          [('P', 12, 190)],
          [('J', 12, 190), ('V', 252, 196), ('K', 498, 214), ('E', 762, 186)],
          [('T', 12, 190)]],
    tall=[[('S', *L), ('P', *R)], [('W', *C)], [('R', *C)], [('C', *C)], [('E', *C)], [('K', *C)], [('V', *C)],
          [('J', *C)], [('T', *C)]],
)

DIAGRAMS = {d.name: d for d in (PIPELINE, ISOLATION, CALIBRATION, DIRECT, INSIGHT)}


def figure(name: str) -> str:
    """One diagram, in its wide and its tall layout; CSS shows the one that fits."""
    if name == GRADING.name:
        svgs = _sequence_wide(GRADING) + _sequence_tall(GRADING)
    else:
        svgs = _flow_svg(DIAGRAMS[name], 'wide') + _flow_svg(DIAGRAMS[name], 'tall')
    return f'<figure class="flow" id="flow-{name}">{svgs}</figure>'
