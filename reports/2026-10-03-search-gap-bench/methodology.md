# GAP methodology

GAP grades completed jobs against captured primary sources. The initial corpus
contained ten briefs: six decision and four research. Calibration is per harness
and model: five repetitions of floor (no search) and ceiling (answer excerpt).
Admission requires floor at most one pass and ceiling at least four passes.
Seven Claude tasks and six Codex tasks were admitted. Code tasks were not run in
this battery. The committed calibration file is an admission summary, not a
runtime calibration file with captured oracle sources.

Each admitted task ran three times on nine arms. Brief grading requires decision
correctness, weighted key-fact recall at least 0.7 and unsupported-claim rate at
most 0.25. Two blinded judges used Claude Code as primary and Codex as secondary.
Gap closure is (arm pass rate − floor pass rate) / (ceiling pass rate − floor pass
rate); it can exceed one. Wilson 95% intervals accompany pass rates. Gap-closure
intervals use a seeded paired task bootstrap with 10,000 resamples. Exact McNemar
tests pair task and repetition, against floor and native. The findings publish
floor comparisons and the largest Codex native comparison, not every native test.

Tokens per success sum all agent tokens in an arm, including failures, divided by
passes. Judge and calibration spend is separate. A zero-success floor has undefined
tokens per success. Vendor spend was unmetered; tokens are not dollar cost.

GAPJUDGE-01 changed readable source extraction, redirects, gzip, source budget
handling and blinding. GAPRULE-01 raised the unsupported-claim limit from 0.10 to
0.25; recomputing saved judge labels flipped 49 failures to passes. These grading
changes did not rerun agents. Execution changes and the later rerun of 31 Codex
cells with unavailable provider tools are preserved separately in REPORT.md.

See [recorded results](REPORT.md), [summary](summary.json),
[admission counts](calibration.json) and [reproduction](reproduce.md).
