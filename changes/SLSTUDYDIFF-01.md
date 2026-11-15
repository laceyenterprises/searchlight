# SLSTUDYDIFF-01 — study data changes produce reviewable diffs

The insight dashboard embeds its summary with `indent=0`, keeping JSON values
on separate lines. A claim text edit now changes only its JSON line instead of
rewriting a multi-megabyte line in both the report dashboard and hosted study.
The data remains embedded, with the existing `<` escape, so the dashboard still
opens from `file://` without network access.

The published dashboard is regenerated with `sew.insight.dashboard.render_dashboard`
from its unchanged `summary.json`, then `python3 scripts/build_site.py` rebuilds
the hosted study. Synthetic tests check JSON equality, script-block preservation,
small diffs and bounded line lengths; the existing dashboard behavior tests
continue to exercise the embedded data.

The diff test uses `unified_diff(n=0)` to count changed lines without fuzzy
intraline matching. Its line-length measurements split on `\n`, matching git;
the fixture includes U+2028, U+2029 and U+0085 to cover Unicode separators
that Python's `splitlines()` would otherwise count as extra lines.

The repository contains one published `reports/*/dashboard.html` and one
hosted `site/study-*.html`; both embed the unchanged summary as multiline JSON.
Existing report checks compare every discovered insight dashboard with its
rendered summary, and the site currency check covers the hosted page. The
roughly 5.5% page-size increase is accepted for reviewable diffs; serialization
keeps the default space after colons.
