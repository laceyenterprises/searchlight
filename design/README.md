# Searchlight design system

How Searchlight looks and talks: colour, type, glyphs, the mark and the words. The site
([searchlightai.dev](https://searchlightai.dev)), the hosted study dashboards and the share card all draw from it.
Searchlight is a Lacey Enterprises project, so it carries Lacey blue.

| File | What it holds |
|---|---|
| [`tokens.css`](tokens.css) | Every colour, font, space and radius token, light and dark. The source of truth: `scripts/build_site.py` inlines it into every page, and the study dashboard template carries a verbatim copy (a test keeps them equal). |
| [`vocabulary.md`](vocabulary.md) | Searchlight's words for its ideas, in plain language. The site's concept tiles, Learn glossary and methodology glossary are generated from its tables. |
| [`glyphs/`](glyphs/) | The glyph sprite, its generator, its index and a catalogue page, plus the three logo options. |

The Searchlight name and mark are trademarks of Lacey Enterprises; the Apache 2.0 licence covers the code and the
glyphs, not the marks. The glyph grammar follows Agent Paddock's (another Lacey Enterprises project); no Paddock mark,
lockup or racing subject is reused.

## Principles

1. **One question, one answer, one action.** Every screen leads with its takeaway in one sentence, then a ranked,
   glanceable view, then one primary action. Detail is one tap away, never in the way.
2. **One accent.** Lacey blue marks what matters: a link, the highlighted series, the beam, the accent stroke of a
   glyph. Everything else is ink and grey.
3. **Colour carries meaning or nothing.** Green (verified), red (unsupported) and amber (a caveat applies) are
   signals. They never decorate, never stand for a series, and always come with a word.
4. **Numbers are honest.** A pass rate says what it averages, shows its 95% range and comes from a committed report,
   transcribed by the generator. Overlapping ranges are a tie, and the page says so. Counts without ranges say so.
5. **Caveats sit beside the number they qualify**, as a short note that opens, not as a wall up front.
6. **The beam explains, never decorates.** Searchlight's one motif is a lamp casting a beam onto the page that holds
   the answer. It appears only where it shows search finding something.
7. **Light and dark are both first class.** Every token has a dark value chosen for the dark ground. Text tokens meet
   WCAG AA (4.5:1) on paper and soft grounds in both themes; a test checks it.
8. **Phones first.** Nothing under 14 px, no horizontal page scroll, rows that read at 390 px.

## Colour

Use the token, never the hex.

| Role | Token | Light | Dark |
|---|---|---|---|
| Page | `--paper` | `#FFFFFF` | `#0C0F13` |
| Raised or soft ground | `--soft` | `#F4F6F9` | `#141920` |
| Rules and borders | `--rule` | `#E3E7EC` | `#262C35` |
| Text | `--ink` | `#111111` | `#F2F4F7` |
| Secondary text | `--ink-2` | `#4F5761` | `#B9C1CC` |
| Labels and captions | `--muted` | `#5F6873` | `#939CAA` |
| **Lacey blue** (marks, dots, bars) | `--blue` | `#007BFF` | `#3D9BFF` |
| Blue text, the primary button | `--blue-ink` | `#0057B3` | `#7DBBFF` |
| Blue wash | `--blue-soft` | `#EAF3FF` | `#0F2238` |
| Verified | `--go` | `#00A86B` | `#2BD99A` |
| Unsupported | `--red` | `#E5202E` | `#FF4D4F` |
| A caveat applies | `--amber` | `#F5B700` | `#FFC83D` |
| The night ground (hero, beam art, share card) | `--night` | `#0C0F13` | `#141920` |

**Data:** `--viz-track` is the 0–100% track, `--viz-interval` the 95% range, `--viz-other` any series that is not
highlighted (controls, references), `--viz-ref` the reference marks (no search, the answer handed over).

## Type

| Use | Family | Notes |
|---|---|---|
| Titles, big numbers | Inter (`--font-display`) | 700 to 750, tracking −0.03 to −0.04 em; numbers use tabular figures |
| Body | Inter (`--font-body`) | 17 px on the site, 400 and 600 |
| Eyebrows, labels, axis ticks | JetBrains Mono (`--font-mono`) | 14 px, upper case, tracking 0.08 em |

The brand in the header is the mark at 40 px beside the word at 23 px / 700.

## Space, radius, depth

Space steps on a 4 px base, `--space-1` (4) to `--space-9` (80). Radius: `--radius-sm` 6 (chips), `--radius-md` 8
(controls), `--radius-lg` 12 (cards, boards), `--radius-xl` 18 (the hero). Depth comes from grounds and rules, not
shadows.

## Glyphs

[`glyphs/searchlight-glyphs.svg`](glyphs/searchlight-glyphs.svg) holds the line glyphs, drawn in code by
[`glyphs/searchlight_glyphs.py`](glyphs/searchlight_glyphs.py): who is tested, the job, running it fairly, grading,
reading the results, the publication, the interface and the beam.

- **Grammar:** a 24 px grid, 2 px strokes in `currentColor`, butt caps, mitred joins, at most one accent.
- **Accent:** reads `--sl-accent` (Lacey blue). Signals read `--sl-go`, `--sl-red` and `--sl-amber`.
- **Sizes:** drawn for 24 px; legible at 16, 32 and 48.
- **Use:** the generator inlines the sprite once per page; a glyph is `<svg class="g"><use href="#verified"/></svg>`,
  decorative, beside the word that carries its meaning.
- **Build:** `python3 design/glyphs/searchlight_glyphs.py` writes the sprite and `index.json`;
  `python3 design/glyphs/build_sheet.py` writes the catalogue page [`glyphs/glyphs.html`](glyphs/glyphs.html).

## The mark

Three options, drawn on the glyph grid in ink plus one Lacey blue beam (see the catalogue page):

- **`logo-lamp` (the default):** a round lamp casting one wide beam up and to the right. It keeps the v1 mark's idea
  and makes it bold enough for 16 px.
- **`logo-lens`:** the search lens, lit: a magnifier ring with the beam leaving it.
- **`logo-tile`:** the lamp and beam on a solid ink tile, the strongest at favicon size.

`LOGO_ID` in `scripts/build_site.py` picks one; the header, footer, favicon (`site/logo.svg`), study pages and share
card follow it. After changing it, run `python3 scripts/build_site.py` and `python3 scripts/render_social_card.py`.

## Components

| Component | Recipe |
|---|---|
| Hero | Night ground, `--radius-xl`; mono eyebrow, a question as the title, one answer line with its numbers in blue, one button, the beam picture |
| Ranked row | Rank, name (with one small meta line), the big number, a 0–100% track with the 95% range and a dot, then the range and `k of n`. A dashed line marks the leader's lower bound. Controls are grey. |
| Note | A caveat chip (amber glyph plus one or two words) that opens to one or two sentences, placed beside the number it qualifies |
| Board | A card of ranked rows: agent and model, runs per setup, its takeaway sentence, then "What the numbers mean" behind a tap |
| Concept tile | A glyph and a term; selecting it opens one short explanation. Without JavaScript every explanation shows. |
| How to read these results | One panel with the method glyph, placed wherever results are ranked |
| Filters | Selects above the ranked views, hidden without JavaScript (the page already shows everything) |
