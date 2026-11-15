# SLTABLE-01 — on a phone, "Every number, by service" reads as one card per service

The insight dashboard's leaderboard (`lib/python/sew/insight/dashboard.html`) now has a phone layout. The study
page and the report's `dashboard.html` are regenerated from it.

- At 640 px wide and narrower, the ten-column table is replaced by one card per visible service. Each card shows the
  rank in the current order, the service name with its tags, and any note. Weighted is shown large, with its range
  and a bar on the same scale as every other card. The other eight metrics sit in a two-column `<dl>`, each with
  its range or n. The table and the cards format every figure with one function (`fig`), so the strings are the
  same.
- A labelled "Sort by" select offers the table's ten sort keys. It shares the table's sort state: the same default
  (Weighted, highest first), the same default direction per key, missing values last and ties by service name. A
  reverse button does what a second click on a column header does. The sorted metric is marked on each card.
  Weighted's hero label is marked for the default sort and when reselected. Selecting a sort restores focus without
  scrolling on fine-pointer devices; coarse-pointer devices skip focus restoration to avoid reopening native pickers.
- If Unique has no confidence interval (absent or null), the cards and table show its count without a range.
- From 641 px the table keeps its columns, sort buttons and `aria-sort`, and its HTML is unchanged. The one change is
  that a service's note now wraps under its name. Before, `th{white-space:nowrap}` also applied to row headers, so
  the long notes on brave.research and claude-native.agent stretched the Service column to about 1,010 px at every
  width and pushed the other nine columns out of view. Cell side padding drops from 10 px to 9 px so the table fits
  whole once the page reaches its full width (about 1,160 px and wider). Between 641 px and that, it scrolls sideways
  in its own box, as before.
- On a phone, the "Full data" box keeps its top and bottom rules but gives up its side border and padding. Every
  tab's panel now spans the page gutter (20 px each side) instead of being inset by about 39 px.
- `tests/test_dashboard_leaderboard.py` runs the dashboard script in Node against a stand-in DOM. It checks the
  cards against the table rows, the select against the header sort, the active metric label, missing Unique ranges,
  focus restoration for fine and coarse pointers, and the service filter. It is skipped where Node is not installed.
- No number, range, count, cost, label meaning or caveat changed. The embedded summary data is byte-identical.
