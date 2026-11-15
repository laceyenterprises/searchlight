"""The insight dashboard's leaderboard: the wide table and the phone cards show the same figures in the same order.

The dashboard script runs in Node against a small stand-in for the DOM: it records the HTML each view renders and
the listeners each view binds, so a test can change the sort or the service filter the way a reader would and read
the next rendering. Skipped where Node is not installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

import pytest

from sew.insight.dashboard import render_dashboard

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'reports' / '2026-11-11-software-factory-insight-bakeoff'
NODE = shutil.which('node')
KEYS = ['arm', 'weighted', 'weighted_per_question', 'insights', 'unique_insights', 'share_of_pool',
        'unsupported_rate', 'cost_usd', 'cost_per_unique_insight', 'latency_p50_ms']
HEADERS = ['Service', 'Weighted', 'Per question', 'Insights', 'Unique', 'Share', 'Unsupported', 'Cost',
           '$ / unique', 'p50']

# A stand-in DOM: every element exists, renders are captured from the views element's innerHTML, and the listener
# a view binds is kept so a step can fire it. Steps: ["select", key], ["reverse"], ["header", key], ["chip", arm].
HARNESS = r'''
const fs = require("fs");
const page = fs.readFileSync(process.argv[2], "utf8");
const steps = JSON.parse(process.argv[3]);
const coarsePointer = JSON.parse(process.argv[4]);
const data = page.match(/<script type="application\/json" id="data">([\s\S]*?)<\/script>/)[1];
const code = page.slice(page.lastIndexOf("<script>") + 8, page.lastIndexOf("</script>"));
const bound = {};
const focused = [];
function El(key) { this.key = key; this.attrs = {}; this.listeners = {}; this.innerHTML = ""; this.textContent = "";
  this.value = ""; this.style = {}; this.tabIndex = 0; }
El.prototype.addEventListener = function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn);
  bound[this.key] = this; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
El.prototype.focus = function (options) { focused.push({ key: this.key, options }); };
El.prototype.querySelector = function (sel) { return new El(sel); };
El.prototype.querySelectorAll = function (sel) {
  if (sel !== "[data-sort]") return [];
  return [...this.innerHTML.matchAll(/data-sort="([^"]+)"/g)].map(m => {
    const e = new El("sort:" + m[1]); e.attrs["data-sort"] = m[1]; return e; });
};
const ids = {};
function byId(id) { return ids[id] || (ids[id] = new El("#" + id)); }
byId("data").textContent = data;
global.document = { getElementById: byId, querySelector: () => null, documentElement: new El("html") };
global.localStorage = { getItem: () => null, setItem() {} };
global.matchMedia = query => ({ matches: query === "(pointer: coarse)" && coarsePointer });
global.history = { replaceState() {} };
global.location = { hash: "#leaderboard" };
eval(code);
const fire = (key, type, event) => bound[key].listeners[type].forEach(fn => fn(event || {}));
const snapshot = () => ({ html: byId("views").innerHTML, focused: focused.splice(0) });
const out = [snapshot()];
for (const [what, arg] of steps) {
  if (what === "select") { bound["#lbsort"].value = arg; fire("#lbsort", "change"); }
  else if (what === "reverse") fire("#lbdir", "click");
  else if (what === "header") fire("sort:" + arg, "click");
  else if (what === "chip") fire("#chips", "click", { target: { closest: () => ({ getAttribute: () => arg }) } });
  out.push(snapshot());
}
process.stdout.write(JSON.stringify(out));
'''


class Leaderboard(HTMLParser):
    """Reads one rendering: the table's header and rows, the sort options and the cards, as text."""

    def __init__(self, html: str, focused: list):
        super().__init__()
        self.focused = focused
        self.head: list[dict] = []
        self.rows: list[list[str]] = []
        self.options: list[tuple[str, str]] = []
        self.selected = None
        self.cards: list[dict] = []
        self._stack: list[str] = []
        self._text: list[str] | None = None
        self._where = ''
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get('class') or ''
        if tag == 'thead':
            self._where = 'head'
        elif tag == 'tbody':
            self._where = 'body'
        elif tag == 'tr' and self._where == 'body':
            self.rows.append([])
        elif tag == 'th' and self._where == 'head':
            self.head.append({'aria-sort': a.get('aria-sort'), 'key': None, 'label': ''})
        elif tag == 'button' and self._where == 'head' and 'data-sort' in a:
            self.head[-1]['key'] = a['data-sort']
            self._text = []
        elif tag in ('th', 'td') and self._where == 'body':
            self._text = []
        elif tag == 'option':
            self.options.append((a['value'], ''))
            if 'selected' in a:
                self.selected = a['value']
            self._text = []
        elif tag == 'li' and 'lbcard' in cls:
            self._where = 'card'
            self.cards.append({'rank': '', 'name': '', 'pairs': [], 'on': None})
        elif self._where == 'card' and tag == 'span':
            if cls == 'rk':
                self._text = []
            self._stack.append('rk' if cls == 'rk' else 'span')
        elif self._where == 'card' and tag == 'h3':
            self._text = []
        elif self._where == 'card' and tag == 'div' and 'on' in cls.split():
            self.cards[-1]['on'] = len(self.cards[-1]['pairs'])
        elif self._where == 'card' and tag in ('dt', 'dd'):
            self._text = []
        if tag == 'p' and self._where in ('body', 'card'):
            self._stack.append('note')  # a service note is text, not a figure

    def handle_endtag(self, tag):
        if tag == 'p' and self._stack and self._stack[-1] == 'note':
            self._stack.pop()
        text = ''.join(self._text or [])
        if tag == 'button' and self._where == 'head' and self._text is not None:
            self.head[-1]['label'] = text
            self._text = None
        elif tag in ('th', 'td') and self._where == 'body' and self._text is not None:
            self.rows[-1].append(text)
            self._text = None
        elif tag == 'option':
            self.options[-1] = (self.options[-1][0], text)
            self._text = None
        elif tag == 'span' and self._where == 'card' and self._stack and self._stack[-1] in ('rk', 'span'):
            if self._stack.pop() == 'rk':
                self.cards[-1]['rank'] = text
                self._text = None
        elif self._where == 'card' and tag == 'h3':
            self.cards[-1]['name'] = text
            self._text = None
        elif self._where == 'card' and tag == 'dt':
            self.cards[-1]['pairs'].append([text, None])
            self._text = None
        elif self._where == 'card' and tag == 'dd':
            self.cards[-1]['pairs'][-1][1] = text
            self._text = None
        elif tag == 'li' and self._where == 'card':
            self._where = ''
        elif tag in ('thead', 'tbody'):
            self._where = ''

    def handle_data(self, data):
        if self._text is not None and 'note' not in self._stack:
            self._text.append(data)


@pytest.fixture(scope='module')
def study():
    summary = json.loads((STUDY / 'summary.json').read_text())
    return summary, render_dashboard(summary)


def run(tmp_path, html: str, steps: list, *, coarse_pointer=False) -> list[Leaderboard]:
    if not NODE:
        pytest.skip('node is not installed')
    page, harness = tmp_path / 'dashboard.html', tmp_path / 'harness.js'
    page.write_text(html)
    harness.write_text(HARNESS)
    done = subprocess.run([NODE, str(harness), str(page), json.dumps(steps), json.dumps(coarse_pointer)],
                          capture_output=True, text=True, timeout=60, check=True)
    return [Leaderboard(frame['html'], frame['focused']) for frame in json.loads(done.stdout)]


def names(board: Leaderboard) -> list[str]:
    return [card['name'].split(' ')[0].strip() for card in board.cards]


def table_names(board: Leaderboard) -> list[str]:
    return [row[0].split(' ')[0].strip() for row in board.rows]


def expected(summary, key: str, direction: int, arms=None) -> list[str]:
    """The table's order: by the key, ties by service name, missing values last in the filter's order."""
    arms = list(arms or summary['ranked'])
    if key == 'arm':
        return sorted(arms, reverse=direction < 0)
    value = {a: summary['metrics'][a][key]['value'] for a in arms}
    known = sorted((a for a in arms if value[a] is not None), key=lambda a: (direction * value[a], a))
    return known + [a for a in arms if value[a] is None]


def test_the_phone_layout_has_one_card_per_service_with_every_figure_the_table_row_has(tmp_path, study):
    summary, html = study
    board, = run(tmp_path, html, [])
    assert len(board.cards) == len(board.rows) == len(summary['ranked'])
    assert names(board) == table_names(board) == expected(summary, 'weighted', -1)
    for rank, (card, row) in enumerate(zip(board.cards, board.rows), start=1):
        assert card['rank'] == f'Rank {rank}'
        assert card['name'] == row[0]  # the name with its tags, such as "imported"
        assert [label for label, _ in card['pairs']] == [
            'Weighted', 'Per question', 'Insights', 'Unique', 'Share of pool', 'Unsupported', 'Cost',
            'Cost per unique', 'p50 latency']
        # Every value with its range or n, character for character as the table cell shows it.
        assert [value for _, value in card['pairs']] == row[1:]
    imported = [c for c in board.cards if 'imported' in c['name']]
    assert imported and all(re.fullmatch(r'\S+ imported', c['name']) for c in imported)


def test_the_phone_sort_offers_the_table_keys_and_orders_the_same_way(tmp_path, study):
    summary, html = study
    boards = run(tmp_path, html, [['select', 'cost_usd'], ['reverse'], ['select', 'arm'], ['reverse'],
                                  ['select', 'unsupported_rate'], ['header', 'cost_usd'], ['header', 'cost_usd'],
                                  ['select', 'weighted']])
    default, cost, cost_up, arm, arm_down, unsupported, header_cost, header_cost_up, weighted = boards
    assert [k for k, _ in default.options] == [h['key'] for h in default.head] == KEYS
    assert default.options[1] == ('weighted', 'Weighted')
    for board, key, direction in ((default, 'weighted', -1), (cost, 'cost_usd', -1), (cost_up, 'cost_usd', 1),
                                  (arm, 'arm', 1), (arm_down, 'arm', -1), (unsupported, 'unsupported_rate', -1),
                                  (header_cost, 'cost_usd', -1), (header_cost_up, 'cost_usd', 1),
                                  (weighted, 'weighted', -1)):
        assert names(board) == table_names(board) == expected(summary, key, direction), key
        assert [k for k, _ in board.options] == KEYS and board.selected == key
        marked = [h['key'] for h in board.head if h['aria-sort'] != 'none']
        assert marked == [key]
        active_metric = KEYS.index(key) - 1 if key != 'arm' else None
        assert all(card['on'] == active_metric for card in board.cards)
    # The phone control and the table headers are one sort: the same key, the same direction.
    assert names(cost) == names(header_cost) and names(cost_up) == names(header_cost_up)
    assert cost.cards[0]['on'] == 6 and arm.cards[0]['on'] is None  # the sorted metric is marked on each card
    assert any(v is None for v in (summary['metrics'][a]['cost_usd']['value'] for a in summary['ranked']))


@pytest.mark.parametrize('missing', [True, False], ids=['absent-ci', 'null-ci'])
def test_unique_counts_render_without_confidence_intervals(tmp_path, study, missing):
    summary = deepcopy(study[0])
    for metrics in summary['metrics'].values():
        if missing:
            metrics['unique_insights'].pop('ci95', None)
        else:
            metrics['unique_insights']['ci95'] = None
    board, = run(tmp_path, render_dashboard(summary), [])
    assert names(board) == table_names(board) == expected(summary, 'weighted', -1)
    for card, row, arm in zip(board.cards, board.rows, names(board)):
        value = str(summary['metrics'][arm]['unique_insights']['value'])
        assert card['pairs'][3] == ['Unique', value]
        assert row[4] == value


@pytest.mark.parametrize('coarse_pointer', [False, True], ids=['keyboard', 'touch'])
def test_sort_select_restores_focus_without_scroll_only_for_fine_pointers(tmp_path, study, coarse_pointer):
    summary, html = study
    default, cost, weighted = run(tmp_path, html, [['select', 'cost_usd'], ['select', 'weighted']],
                                  coarse_pointer=coarse_pointer)
    assert default.focused == []
    for board, key in ((cost, 'cost_usd'), (weighted, 'weighted')):
        assert board.selected == key
        assert names(board) == table_names(board) == expected(summary, key, -1)
        assert board.focused == ([] if coarse_pointer else [
            {'key': '#lbsort', 'options': {'preventScroll': True}}])


def test_the_service_filter_applies_to_the_cards_and_the_table(tmp_path, study):
    summary, html = study
    first = summary['ranked'][0]
    _, without, back = run(tmp_path, html, [['chip', first], ['chip', first]])
    rest = [a for a in summary['ranked'] if a != first]
    assert names(without) == table_names(without) == expected(summary, 'weighted', -1, rest)
    assert names(back) == expected(summary, 'weighted', -1)


def test_the_wide_table_keeps_its_columns_sort_buttons_and_aria_sort(tmp_path, study):
    _, html = study
    board, = run(tmp_path, html, [])
    assert [h['label'] for h in board.head] == HEADERS
    assert [h['aria-sort'] for h in board.head] == ['none', 'descending'] + ['none'] * 8
    assert all(len(row) == 10 for row in board.rows)


def test_below_641_px_the_cards_replace_the_table_and_the_panel_uses_the_page_gutter(study):
    _, html = study
    style = html[html.index('<style>'):html.index('</style>')]
    phone = style[style.index('@media (max-width:640px){'):]
    assert '.lbphone{display:none}' in style[:style.index('@media (max-width:640px){')]
    assert '.lbwide{display:none}.lbphone{display:block}' in phone
    assert '.fulldata>.in{padding:0 0 18px}' in phone  # the full-data box no longer insets the panels
    assert '<label for="lbsort">Sort by</label><select id="lbsort">' in html
