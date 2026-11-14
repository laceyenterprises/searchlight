# SLTEACH-01 — every block makes its point in ten seconds, with a picture

The site's copy is tightened page by page, and each block now carries a picture of its point.

- `scripts/searchlight_site/pictures.py` draws small inline SVG pictures from the design tokens and the glyph
  sprite: the gap (no search against every search setup), overlapping leader ranges, memory against the best
  provider with tokens per pass, keywords against plain questions, a different leader per API job, a task's place
  against the training cutoff, and a study's cumulative coverage. Each is `role="img"` with an `aria-label` that
  says the same in words, and every number in it is read from the published data.
- Five glyphs join the set (53): `memory`, `keywords`, `question`, `primary-source`, `combine`. They are in the
  module, `index.json`, the sprite and `glyphs.html`.
- The overview gains "Building your own agent knowledge system?": three rules of thumb, each with its number and a
  link to its report. Rules a finding row already makes are left out.
- "So what:" becomes "For your agent:" (`components.lesson`). Report pages open with an "In ten seconds" panel:
  the headline sentence, the headline chart and, where it adds something, one lesson.
- Studies lead with the coverage picture and its claim, computed from the study's "If you could only buy k" table,
  so a corrected table moves both. Studies without usable coverage show the descriptive deck instead.
- Empty coverage and tie inputs omit their SVG pictures. Report token lessons skip rows without token counts.
- The learn walkthrough's notes are "Eval lesson:" lines. Methodology sections lead with a claim under a topic
  eyebrow.
- No result, range, count or cost changed; `leaderboard.json` and `reports/` are unchanged.
