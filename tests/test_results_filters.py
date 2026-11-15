"""The results page's filters: every chip changes what sits directly under the chips.

The site script runs in Node against a small DOM built from the built results page: elements, attributes, radios and
`hidden`, with the handful of selectors the script uses. Each case loads the page with one chip in the query string,
the way a shared link or a tap would, and reads which rows are left visible. Skipped where Node is not installed.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('build_site', ROOT / 'scripts' / 'build_site.py')
site = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(site)
NODE = shutil.which('node')
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}


class Tree(HTMLParser):
    """The page as nested [tag, attrs, children]; text is kept only where the script or a test reads it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = ['#root', {}, []]
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = [tag, {k: (v if v is not None else '') for k, v in attrs}, []]
        self.stack[-1][2].append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1][2].append(data)


HARNESS = r'''
const fs = require("fs");
const tree = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const code = fs.readFileSync(process.argv[3], "utf8");
const queries = JSON.parse(process.argv[4]);

function compound(text) {
  const parts = { tag: null, cls: [], attrs: [], scope: false };
  const re = /^(\*|[a-z][a-z0-9]*)|\.([\w-]+)|#([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]|(:scope)/g;
  let m, at = 0;
  while ((m = re.exec(text)) && m.index === at) {
    at = re.lastIndex;
    if (m[1]) parts.tag = m[1]; else if (m[2]) parts.cls.push(m[2]); else if (m[3]) parts.attrs.push(["id", m[3]]);
    else if (m[4]) parts.attrs.push([m[4], m[5]]); else parts.scope = true;
  }
  if (at !== text.length) throw new Error("selector not supported: " + text);
  return parts;
}
function parse(sel) {
  return sel.split(",").map(s => {
    const steps = [], tokens = s.trim().replace(/\s*>\s*/g, " > ").split(/\s+/);
    let comb = " ";
    tokens.forEach(t => { if (t === ">") comb = ">"; else { steps.push({ comb, c: compound(t) }); comb = " "; } });
    return steps;
  });
}
function one(el, c, scope) {
  if (c.scope) return el === scope;
  if (c.tag && c.tag !== "*" && el.tagName !== c.tag) return false;
  if (c.cls.some(k => !el.classList.contains(k))) return false;
  return c.attrs.every(([k, v]) => el.hasAttribute(k) && (v === undefined || el.getAttribute(k) === v));
}
function matches(el, steps, scope, i = steps.length - 1) {
  if (!one(el, steps[i].c, scope)) return false;
  if (i === 0) return true;
  if (steps[i].comb === ">") return !!el.parentElement && matches(el.parentElement, steps, scope, i - 1);
  for (let p = el.parentElement; p; p = p.parentElement) if (matches(p, steps, scope, i - 1)) return true;
  return false;
}

function El(node, parent) {
  this.tagName = node[0]; this.attrs = node[1]; this.parentElement = parent && parent.tagName !== "#root" ? parent : null;
  this.children = []; this.text = ""; this.listeners = {};
  this.hidden = "hidden" in this.attrs; this.open = "open" in this.attrs; this._checked = "checked" in this.attrs;
  this.value = this.attrs.value || "";
  const el = this;
  this.classList = {
    contains: k => (el.attrs.class || "").split(/\s+/).indexOf(k) >= 0,
    toggle: (k, on) => { const s = new Set((el.attrs.class || "").split(/\s+/).filter(Boolean));
      if (on) s.add(k); else s.delete(k); el.attrs.class = Array.from(s).join(" "); },
  };
  node[2].forEach(child => { if (typeof child === "string") this.text += child; else this.children.push(new El(child, this)); });
}
El.prototype.hasAttribute = function (k) { return k in this.attrs; };
El.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.addEventListener = function (type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); };
El.prototype.walk = function (fn) { this.children.forEach(c => { fn(c); c.walk(fn); }); };
El.prototype.querySelectorAll = function (sel) {
  const lists = parse(sel), out = [];
  this.walk(el => { if (lists.some(steps => matches(el, steps, this))) out.push(el); });
  return out;
};
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
El.prototype.closest = function (sel) {
  const lists = parse(sel);
  for (let el = this; el; el = el.parentElement) if (lists.some(steps => matches(el, steps, null))) return el;
  return null;
};
Object.defineProperty(El.prototype, "id", { get() { return this.attrs.id || ""; } });
Object.defineProperty(El.prototype, "textContent", { get() {
  let t = this.text; this.children.forEach(c => { t += c.textContent; }); return t; } });
Object.defineProperty(El.prototype, "checked", {
  get() { return this._checked; },
  set(on) {  // a radio unchecks the others of its name, as a browser does
    if (on && this.attrs.type === "radio") {
      const form = this.closest("form");
      (form ? form.querySelectorAll('input[name="' + this.attrs.name + '"]') : []).forEach(r => { r._checked = false; });
    }
    this._checked = !!on;
  } });
Object.defineProperty(El.prototype, "elements", { get() {
  const form = this, named = {};
  this.querySelectorAll("input").forEach(r => { named[r.attrs.name] = true; });
  Object.keys(named).forEach(name => { named[name] = { get value() {
    const on = form.querySelectorAll('input[name="' + name + '"]').filter(r => r.checked)[0];
    return on ? on.value : ""; } }; });
  return named;
} });

function visible(el) {
  for (let e = el, child = null; e; child = e, e = e.parentElement) {
    if (e.hidden) return false;
    if (e.tagName === "details" && !e.open && child && child.tagName !== "summary") return false;
  }
  return true;
}

const results = queries.map(search => {
  const root = new El(tree, null), doc = root.children.filter(c => c.tagName === "html")[0] || root;
  const byId = {};
  root.walk(el => { if (el.attrs.id && !(el.attrs.id in byId)) byId[el.attrs.id] = el; });
  global.document = { getElementById: id => byId[id] || null, querySelectorAll: s => doc.querySelectorAll(s),
                      addEventListener() {} };
  global.location = { search, pathname: "/results.html", hash: "" };
  global.history = { replaceState() {} };
  new Function(code)();
  const main = root.querySelector("main");
  const sections = main.querySelectorAll("section.finding").filter(visible).map(s => s.attrs.id);
  const rows = main.querySelectorAll(".row, [data-f]").filter(visible).map(el => {
    const pc = el.querySelector(".pc");
    return { section: (el.closest("section.finding") || { attrs: {} }).attrs.id || "", id: el.attrs.id || "",
             agent: el.attrs["data-agent"] || "", bench: el.attrs["data-bench"] || "",
             provider: el.attrs["data-provider"] || "", lead: "data-lead" in el.attrs, chosen: "data-chosen" in el.attrs,
             pc: pc ? pc.textContent : "", label: el.attrs.class === "row" || /\brow\b/.test(el.attrs.class || "")
               ? (el.querySelector(".track") || { attrs: {} }).attrs["aria-label"] || "" : "" };
  });
  return { search, sections, rows };
});
process.stdout.write(JSON.stringify(results));
'''


@pytest.fixture(scope='module')
def page():
    return site.build()['results.html']


@pytest.fixture(scope='module')
def data(page):
    """The page's own filter data: one entry per ranked row."""
    raw = re.search(r'<script type="application/json" id="filter-data">(.*?)</script>', page, re.S).group(1)
    return {row['id']: row for row in json.loads(raw)}


@pytest.fixture(scope='module')
def chips(page):
    form = re.search(r'<form class="filters.*?</form>', page, re.S).group(0)
    return [(k, v) for k, v in re.findall(r'<input type="radio" name="(\w+)" value="([^"]*)"', form) if v]


@pytest.fixture(scope='module')
def run(page, chips, tmp_path_factory):
    if not NODE:
        pytest.skip('Node is not installed')
    tree = Tree()
    tree.feed(page)
    folder = tmp_path_factory.mktemp('filters')
    (folder / 'tree.json').write_text(json.dumps(tree.root))
    (folder / 'harness.js').write_text(HARNESS)
    queries = [''] + [f'?{k}={v}' for k, v in chips]
    done = subprocess.run([NODE, str(folder / 'harness.js'), str(folder / 'tree.json'),
                           str(ROOT / 'scripts' / 'site_assets' / 'site.js'), json.dumps(queries)],
                          capture_output=True, text=True, timeout=60, check=True)
    return {r['search']: r for r in json.loads(done.stdout)}


def _ranked(state):
    return [r for r in state['rows'] if r['section'] == 'ranked']


def test_every_ranked_row_sits_under_the_filters_and_the_findings_list_none_twice(page, data):
    main = page.split('<main id="main">', 1)[1]
    assert main.index('id="filters"') < main.index('id="ranked"') < main.index('id="finding-1"')
    ranked = main[main.index('id="ranked"'):main.index('id="finding-1"')]
    assert ranked.count('<h2>') == 1
    for row_id in data:  # every row of the filter data, with its id, inside the ranked section
        assert f'id="{row_id}"' in ranked, row_id
    assert len(re.findall(r'<li class="row[^"]*" id="r-', main)) == len(data)
    assert 'class="allrows"' not in ranked
    for n in (2, 3):  # a finding repeats its leaders, not an "All n setups" list
        section = main[main.index(f'id="finding-{n}"'):main.index(f'id="finding-{n + 1}"')]
        assert 'class="allrows"' not in section and ' id="r-' not in section and ' id="board-' not in section


def test_every_filter_setting_shows_only_its_rows_in_the_ranking(run, data, chips):
    everything = _ranked(run[''])
    assert sorted(r['id'] for r in everything) == sorted(data)
    leaders = {}
    for row in data.values():
        leaders.setdefault((row['agent'], row['bench']), row['id'])  # filter data lists each board ranked
    for key, value in chips:
        shown = {r['id'] for r in _ranked(run[f'?{key}={value}'])}
        wanted = {i for i, row in data.items() if row[key] == value}
        assert wanted <= shown, (key, value)
        if key == 'provider':  # the chosen setup, beside each of its boards' leaders
            boards = {(data[i]['agent'], data[i]['bench']) for i in wanted}
            assert shown == wanted | {leaders[b] for b in boards}, value
        else:
            assert shown == wanted, (key, value)


def test_every_chip_changes_the_first_section_under_the_filters(run, chips):
    def first(state):
        top = state['sections'][0]
        return top, [(r['id'], r['agent'], r['provider'], r['pc']) for r in state['rows'] if r['section'] == top]
    assert run['']['sections'][0] == 'ranked'
    for key, value in chips:
        state = run[f'?{key}={value}']
        assert state['sections'], (key, value)
        assert first(state) != first(run['']), (key, value)
        top, rows = first(state)
        assert rows, (key, value)  # the first thing under the chips holds rows, never prose alone


def test_a_benchmark_without_a_ranked_board_shows_its_finding_first(run, data, chips):
    for key, value in chips:
        if key == 'bench' and not any(row['bench'] == value for row in data.values()):
            state = run[f'?bench={value}']
            assert 'ranked' not in state['sections']
            assert state['sections'][0].startswith('finding-'), value


def test_a_findings_chosen_setup_row_matches_the_ranking(run, data):
    index = {(row['agent'], row['bench'], row['provider']): i for i, row in data.items()}
    for setup in sorted({row['provider'] for row in data.values()}):
        state = run[f'?provider={setup}']
        ranked = {r['id']: r for r in _ranked(state)}
        copies = [r for r in state['rows'] if r['section'].startswith('finding-') and r['agent'] and r['bench']
                  and r['provider']]
        for r in copies:  # a finding's row reads exactly like its row in the ranking
            original = ranked[index[r['agent'], r['bench'], r['provider']]]
            assert (original['pc'], original['label']) == (r['pc'], r['label']), r
        for board in {(r['agent'], r['bench']) for r in copies}:  # each board shown holds the chosen setup
            if (*board, setup) in index:
                assert any((r['agent'], r['bench'], r['provider']) == (*board, setup) for r in copies), (setup, board)
    native = [r for r in run['?provider=native']['rows'] if r['section'] == 'finding-2' and r['provider'] == 'native']
    assert {r['agent'] for r in native} == {row['agent'] for row in data.values()
                                            if row['provider'] == 'native' and row['bench'] == site.GAP_PRIMARY}


def test_without_a_chosen_setup_a_finding_shows_only_its_leaders(run, chips):
    for search in [''] + [f'?{k}={v}' for k, v in chips if k != 'provider']:
        assert not any(r['chosen'] for r in run[search]['rows']), search


def test_a_finding_with_no_row_for_the_choice_hides(run, data):
    absent = {row['bench'] for row in data.values()} - {row['bench'] for row in data.values()
                                                         if row['provider'] == 'no-search'}
    state = run['?provider=no-search']
    for section in [s for s in state['sections'] if s != 'leaderboard']:  # the record lists reports, not rows
        rows = [r for r in state['rows'] if r['section'] == section]
        assert rows, section
        assert not any(r['bench'] in absent and r['provider'] for r in rows), section
