# WSB methodology

The experiment used Claude Code with `claude-opus-5-5[1m]`, all 18 production tasks,
eight tool arms and three repetitions. The two arm groups ran about ten hours
apart. Arms differ in exposed search tools: no-search, native, or exactly one
provider MCP server. Budgets and contamination checks apply to each cell.

The 14 competitive tasks and four expected-fail tasks have separate denominators.
Expected-fail success means an honest partial answer or decline. Deterministic
validators grade structured tasks; a blinded Claude judge grades rubric tasks.
Single-judge agreement was not measured. Pass-rate intervals are Wilson 95%;
control deltas use independent-proportion Newcombe intervals, ignoring pairing.
Adjacent rankings are not statistically established.

The original bench corrections fixed overly strict date/dollar decline checks,
a prose-versus-URL field mismatch and judge JSON parsing/blinding. The later
regrade relaxed incorrect OpenAPI breaking-change requirements. Both grade the
same stored answers; neither reran agents. REPORT.md retains initial and updated
tables with their original precision.

Historical cost lower bounds sum priced spend and divide by all successes.
The report command instead uses priced-cell successes, so its output can differ.
Unknown vendor spend is not zero; partially priced arms are excluded from cost
ranking. Updated tokens per success use measured tokens from graded cells divided
by measured successes; coverage counts were not retained. Do not multiply the
historical tokens/cell means by attempted-cell counts to derive that update.
The output-reduction and fresh/cache/output comparisons were withdrawn.

See [recorded results](REPORT.md), [summary](summary.json) and
[reproduction](reproduce.md). Calibration was not part of this experiment:
[calibration record](calibration.json).
