#!/usr/bin/env python3
"""Build the glyph catalogue page from the sprite.

    python3 design/glyphs/searchlight_glyphs.py   # the sprite and index.json
    python3 design/glyphs/build_sheet.py          # glyphs.html, next to this file

Every glyph at 48, 32, 24 and 16 px on the light and the dark ground, with its id and meaning. The page links
../tokens.css, so it shows the tokens' accent. Standard library only.
"""
import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GROUPS = [
    ("logo", "The mark", "Three options for the Searchlight mark, drawn on the glyph grid: ink plus one Lacey blue beam. "
     "Each must read at 16 px."),
    ("cast", "Who is tested", "The agent does the job; the search setup is the one thing that changes between runs."),
    ("job", "The job", "What the agent is asked to do, and what it searches with."),
    ("fair", "Running it fairly", "What keeps a comparison honest: the same job, a sealed workspace, repeats and a blind "
     "grader."),
    ("grading", "Grading", "How an answer earns credit. Verified, unsupported and unverifiable are signals: their colour "
     "carries the meaning, and a word always goes with it."),
    ("reading", "Reading the results", "Ranks, ranges, ties and what a result cost."),
    ("publication", "The publication", "Benchmarks, studies, reports and the pages that explain them."),
    ("ui", "Interface", "The menu, open and closed."),
    ("beam", "The beam", "Searchlight's one motif. It appears only where it explains finding something."),
]
CSS = """
body { margin: 0; background: var(--paper); color: var(--ink); font-family: var(--font-body); }
.wrap { max-width: 1180px; margin: 0 auto; padding: 48px 24px 80px; }
.brand { display: flex; align-items: center; gap: 12px; font-size: 26px; font-weight: 700; letter-spacing: -.02em; }
.brand svg { width: 44px; height: 44px; }
h1 { font-size: 44px; letter-spacing: -.03em; margin: 24px 0 8px; }
h2 { font-size: 28px; letter-spacing: -.02em; margin: 48px 0 4px; }
.lede, .sub { color: var(--ink-2); max-width: 760px; line-height: 1.5; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; margin-top: 16px; }
.card { border: 1px solid var(--rule); border-radius: 12px; overflow: hidden; background: var(--paper); }
.grounds { display: grid; grid-template-columns: 1fr 1fr; }
.ground { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 18px 8px; }
.ground.light { background: #FFFFFF; color: #111111; --sl-accent: #007BFF; --paper: #FFFFFF; }
.ground.dark { background: #0C0F13; color: #F2F4F7; --sl-accent: #3D9BFF; --paper: #0C0F13; }
.ground svg { flex: none; }
.s48 { width: 48px; height: 48px; } .s32 { width: 32px; height: 32px; } .s24 { width: 24px; height: 24px; } .s16 { width: 16px; height: 16px; }
.meta { padding: 10px 14px 14px; border-top: 1px solid var(--rule); }
.id { font-family: var(--font-mono); font-size: 13px; font-weight: 500; }
.meaning { margin: 4px 0 0; font-size: 14px; color: var(--ink-2); line-height: 1.4; }
"""


def main():
    sprite = (HERE / "searchlight-glyphs.svg").read_text(encoding="utf-8").replace(
        'style="display:none"', 'style="position:absolute;width:0;height:0" aria-hidden="true"')
    index = json.loads((HERE / "index.json").read_text(encoding="utf-8"))

    def use(gid, size):
        return f'<svg class="s{size}" aria-hidden="true"><use href="#{gid}"/></svg>'

    count = sum(g["group"] != "logo" for g in index)
    parts = ['<!doctype html>', '<html lang="en">', '<head>', '<meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">', '<title>Searchlight glyphs</title>',
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&'
             'family=JetBrains+Mono:wght@400;500&display=swap">',
             '<link rel="stylesheet" href="../tokens.css">', f'<style>{CSS}</style>', '</head>', '<body>', sprite,
             '<div class="wrap">', '<div class="brand"><svg aria-hidden="true"><use href="#logo-lamp"/></svg>'
             'Searchlight</div>', '<h1>Glyphs</h1>',
             f'<p class="lede">{count} line glyphs on a 24 px grid: 2 px strokes, square ends, mitred joins and at '
             'most one accent, in Lacey blue. Each one at 48, 32, 24 and 16 px on the light and the dark ground.</p>']
    for key, title, sub in GROUPS:
        members = [g for g in index if g["group"] == key]
        if not members:
            continue
        parts.append(f'<h2>{html.escape(title)}</h2><p class="sub">{html.escape(sub)}</p><div class="grid">')
        for g in members:
            sizes = "".join(use(g["id"], s) for s in (48, 32, 24, 16))
            parts.append(f'<div class="card"><div class="grounds"><div class="ground light">{sizes}</div>'
                         f'<div class="ground dark">{sizes}</div></div><div class="meta"><div class="id">'
                         f'{html.escape(g["id"])}</div><p class="meaning">{html.escape(g["meaning"])}</p></div></div>')
        parts.append('</div>')
    parts += ['</div>', '</body>', '</html>', '']
    (HERE / "glyphs.html").write_text("\n".join(parts), encoding="utf-8")
    print(f"{len(index)} glyphs -> glyphs.html")


if __name__ == "__main__":
    main()
