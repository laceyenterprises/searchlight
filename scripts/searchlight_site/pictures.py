"""Small pictures that carry a point without the words: inline SVG drawn from the design tokens and the glyph sprite.

Every picture is ``role="img"`` with an ``aria-label`` that says the same thing in words, and every number in it is
read from the data passed in. Colours are the tokens' classes in site.css (``.pic``), so both themes follow."""
from __future__ import annotations

from .data import esc, leader

X0, W = 36.0, 200.0  # the 0-100% track: it starts after a 24 px glyph and its gap


def _x(value: float, scale: float = 100.0) -> float:
    return X0 + W * max(0.0, min(value, scale)) / scale


def _svg(width: float, height: float, label: str, body: str, cls: str = '') -> str:
    return (f'<svg class="pic{" " + cls if cls else ""}" viewBox="0 0 {width:g} {height:g}" role="img" '
            f'aria-label="{esc(label)}">{body}</svg>')


def _glyph(gid: str, x: float, y: float, size: float = 24) -> str:
    return f'<use class="gy" href="#{gid}" x="{x:g}" y="{y:g}" width="{size:g}" height="{size:g}"/>'


def _bar(lo: float, hi: float, y: float, h: float, cls: str, scale: float = 100.0) -> str:
    x0, x1 = _x(lo, scale), _x(hi, scale)
    return (f'<rect class="{cls}" x="{x0:.1f}" y="{y:g}" width="{max(2.0, x1 - x0):.1f}" height="{h:g}" '
            f'rx="{min(h / 2, 6):g}"/>')


def gap(floor: float, lo: float, hi: float) -> str:
    """The cutoff glyph, then one 0-100% track: no search as a dot, every search setup as a band."""
    label = f'No search passed {floor:.0f}%; with any search setup, {lo:.0f} to {hi:.0f}%.'
    body = (_glyph('cutoff', 0, 6) + f'<rect class="tk" x="{X0:g}" y="12" width="{W:g}" height="12" rx="6"/>'
            + _bar(lo, hi, 12, 12, 'pt') + f'<circle class="rf" cx="{max(_x(floor), X0 + 6):.1f}" cy="18" r="6"/>')
    return _svg(240, 36, label, body)


def tie(agents: list[tuple[str, dict, str]]) -> str:
    """Per agent, the leader's 95% range (blue) above every other search setup's (thin): they overlap."""
    if not agents:
        return ''
    body, y, said = [_glyph('tie', 0, 0)], 0.0, []
    for name, board, clause in agents:
        rows = [r for r in board['rows'] if r['arm'] != 'no-search']
        top, others = rows[0], rows[1:]
        body.append(f'<rect class="tk" x="{X0:g}" y="{y:g}" width="{W:g}" height="6" rx="3"/>')
        body.append(_bar(*top['ci'], y, 6, 'pt'))
        for i, r in enumerate(others):
            body.append(_bar(*r['ci'], y + 9 + 3 * i, 2, 'ot'))
        lb = _x(top['ci'][0])
        body.append(f'<path class="dash" d="M{lb:.1f} {y - 2:g}V{y + 9 + 3 * len(others):g}"/>')
        y += 9 + 3 * len(others) + 8
        said.append(f'{name}: {top["label"]} leads, {top["ci"][0]:.0f} to {top["ci"][1]:.0f}%, '
                    f'and {clause}')
    return _svg(240, y - 8, '; '.join(said) + '.', ''.join(body))


def memory(nos: dict, best: dict, clause: str, ratio: float, most: str) -> str:
    """Memory's range against the best provider's, then the tokens each used per pass: 1x against up to the most."""
    label = (f'Memory alone passed {nos["pass_pct"]:.0f}% (range {nos["ci"][0]:.0f} to {nos["ci"][1]:.0f}%) and the '
             f'best provider {best["pass_pct"]:.0f}% ({best["ci"][0]:.0f} to {best["ci"][1]:.0f}%): {clause}. Tokens '
             f'per pass: {nos["tokens_per_success"]} from memory, up to {most} with search, {ratio:.1f} times as many.')
    body = [_glyph('memory', 0, 0, 20), f'<rect class="tk" x="{X0:g}" y="2" width="{W:g}" height="16" rx="6"/>',
            _bar(*nos['ci'], 2, 7, 'ot'), _bar(*best['ci'], 11, 7, 'pt'),
            _glyph('tokens', 0, 26, 20),
            f'<rect class="ot" x="{X0:g}" y="28" width="{W / ratio:.1f}" height="6" rx="3"/>',
            f'<rect class="pt" x="{X0:g}" y="38" width="{W:g}" height="6" rx="3"/>']
    return _svg(240, 46, label, ''.join(body))


def queries(natural: float) -> str:
    """Keyword strings against plain questions: two bars that add up to every logged search."""
    label = f'{natural:.0f}% of searches read as plain questions; {100 - natural:.0f}% were keyword strings.'
    body = (_glyph('keywords', 0, 0) + f'<rect class="ot" x="{X0:g}" y="6" width="{W * (100 - natural) / 100:.1f}" '
            f'height="12" rx="6"/>' + _glyph('question', 0, 28)
            + f'<rect class="pt" x="{X0:g}" y="34" width="{max(4.0, W * natural / 100):.1f}" height="12" rx="6"/>')
    return _svg(240, 52, label, body)


JOB_GLYPH = {'s1': 'insight', 's3': 'source', 's4': 'cost', 's6': 'brief', 's5': 'ranking'}


def jobs(h2h: dict, ids: tuple = ('s1', 's3', 's6')) -> str:
    """One small bar list per job, the leader in blue: a different bar leads in each."""
    measures = {m['id']: m for m in h2h['measures']}
    body, said = [], []
    for col, key in enumerate(ids):
        m = measures[key]
        x, scale = col * 82, m['of'] or max(m['values'].values()) or 1
        top = max(m['values'].values())
        body.append(_glyph(JOB_GLYPH.get(key, 'report'), x, 0, 20))
        for i, provider in enumerate(h2h['providers']):
            if provider in m['values']:
                v = m['values'][provider]
                body.append(f'<rect class="{"pt" if v == top else "ot"}" x="{x:g}" y="{26 + 7 * i:g}" '
                            f'width="{max(2.0, 72 * v / scale):.1f}" height="4" rx="2"/>')
        said.append(f'{m["label"]}: {leader(m)} led with {top}' + (f' of {m["of"]}' if m['of'] else ''))
    return _svg(236, 26 + 7 * len(h2h['providers']), '; '.join(said) + '.', ''.join(body))


def timeline(after: bool, when: str) -> str:
    """Where the task's event sits against the models' training cutoff."""
    label = f'{when}: {"after" if after else "long before"} the models\' training cutoff.'
    x = 204 if after else 34
    body = ('<rect class="tk" x="4" y="22" width="232" height="4" rx="2"/>'
            '<path class="cut" d="M150 14V36"/><text x="150" y="10" text-anchor="middle">cutoff</text>'
            f'<circle class="pt" cx="{x}" cy="24" r="7"/>')
    return _svg(240, 40, label, body, 'line')


def coverage(greedy: list[dict]) -> str:
    """How much of every useful finding the first one, two, three ... services found, best first: each column is
    what came before (light) plus what that service added (blue)."""
    if not greedy:
        return ''
    n = len(greedy)
    gap, width, base, tall = 4.0, 480.0, 132.0, 120.0
    w = (width - gap * (n - 1)) / n
    body, prev = [], 0.0
    for i, step in enumerate(greedy):
        share = step['cumulative_share'] or 0.0
        x = i * (w + gap)
        if prev:
            body.append(f'<rect class="ci" x="{x:.1f}" y="{base - tall * prev:.1f}" width="{w:.1f}" '
                        f'height="{tall * prev:.1f}"/>')
        if share > prev:
            body.append(f'<rect class="pt" x="{x:.1f}" y="{base - tall * share:.1f}" width="{w:.1f}" '
                        f'height="{tall * (share - prev):.1f}"/>')
        prev = share
    body.append(f'<path class="dash" d="M0 {base - tall / 2:g}H{width:g}"/>'  # half of everything found
                f'<rect class="tk" x="0" y="{base:g}" width="{width:g}" height="2"/>')
    label = ('Share of all useful findings found by the best one, two, three and more services together: '
             + ', '.join(f'{s["cumulative_share"]:.0%}' for s in greedy if s['cumulative_share'] is not None) + '.')
    return _svg(width, base + 2, label, ''.join(body), 'wide')
