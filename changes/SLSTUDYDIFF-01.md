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
