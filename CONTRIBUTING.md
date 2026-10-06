# Contributing

Use Python 3.11–3.13 on Linux or macOS. Install with
`python3 -m pip install ".[test]"`, then run `SEW_MODE=standalone python3 -m pytest -q`
and `python3 scripts/lint.py`. Before the full suite, prepare its public,
hash-pinned wheel dependencies outside the repository:

```sh
export SEW_GAP_CODE_WHEELHOUSE=/path/to/gap-wheels
python3 bin/prepare-gap-code-wheelhouse --wheelhouse "$SEW_GAP_CODE_WHEELHOUSE" --download
```

Only setup downloads dependencies; the suite runs offline. Real GAP execution
coverage requires usable Seatbelt or bubblewrap; absence is a test failure.
CI provisions these backends and denies external network for the entire suite.
 The suite uses recorded fixtures; provider keys
and harness logins are unnecessary. GAP task tests are corpus assets, executed
by the verifier rather than collected as workbench tests.

PRs run CI on Linux for Python 3.11–3.13. The weekly
schedule and manual workflow dispatch also run macOS. Superseded runs are
cancelled only within the same trigger type and PR or ref, so a push cannot
cancel scheduled or manual macOS coverage.

Standalone CI exercises a dependency-free, canonical-writer-shaped Codex receipt
with float epoch times and a microsecond `Z` refresh stamp. Real SDK integration
tests remain optional here. After SWX-09, the Agent OS consumer lane should run
`tests/test_broker_auth.py::test_sdk_host_receipt_preserves_document` and
`tests/test_broker_auth.py::test_doctor_codex_auth_check_without_model_turn`
against its installed SDK without skips, so writer-to-consumer drift is caught.

Submit a pull request with the problem, behavior change and test evidence.
New behavior needs a regression test. Preserve catalog receipts and grading
contracts. Keep run bundles, credentials and local configuration outside Git.
Contributions are licensed under Apache-2.0. Reports are maintained in reports/.

Repository administrators must provision the `searchlight-maintainers` GitHub
team with write access to activate the CODEOWNERS review rule before publication.

Publication checks share the existing Ubuntu Python 3.12 matrix lane on main pushes and pull requests; there is no additional publication job or duplicated full suite:

```sh
python3 -m pip install '.[test]'
git fetch --prune origin '+refs/pull/*:refs/remotes/pull/*'
python3 scripts/publication-gates.py hardcodes
python3 scripts/publication-gates.py secrets
python3 scripts/publication-gates.py licences
scripts/public-clone-proof.sh
```

The hardcode denylist scans staged Git blobs, including reports, CI and symlink
target text; unstaged edits and host files cannot mask the indexed content. The
secret gate also scans all reachable blobs, commit messages and annotated tags;
use a full clone (`fetch-depth: 0`) and fetch GitHub's `refs/pull/*` namespace as
shown above, including closed PRs whose branches were deleted. Full checkout
alone fetches branches and tags, not PR refs. CI fetches PR refs with a temporary
read-token header supplied through command-scoped environment configuration (never argv or persisted Git config) and fails closed if neither `refs/remotes/pull/*` nor
`refs/pull/*` exists, or if the fetch fails. It reports locations and rule names without
printing credential values. Exact synthetic scrub-test strings retained in
the imported history are recognized as fixtures (two legacy key/assignment values and three explicit Bearer examples); there are no path allowlists.
The licence gate follows installed public runtime and test dependencies, including
transitive dependencies, and rejects missing or non-permissive licence metadata.
The private optional host extra is outside the standalone publication boundary.

The clone proof makes a separate clone of committed HEAD, installs into a fresh
venv, and runs the full suite and complete lighthouse fixture battery with an empty
environment, isolated home/state, and explicit standalone mode. No repository or
provider secrets are supplied. Checkout uses the platform read token while the
repository remains private; credentials are not persisted or passed to the proof.
SWX-12 owns the visibility change and release decision. Before changing visibility,
the operator must separately account for commits orphaned by force-push or deleted
refs: GitHub may still serve them by SHA even though no advertised ref reaches
them. A green reachable-history gate does not cover those objects. Resolve any
such credential exposure with GitHub Support's sensitive-data purge process, or
publish a fresh repository containing only the audited history. The visibility
handoff requires this check as well as green publication gates.

The fixture proof raises only aggregate smoke budgets in a temporary copy of the
installed catalog, preserving all 168 matrix cells and their existing grades. It
rejects incomplete runs; the committed operator budgets remain unchanged.
The full-battery pytest case is opt-in (`SEW_TEST_PUBLICATION_BATTERY=1`); normal
matrix jobs skip it, and the clone proof runs the dedicated battery script once.
Publication gates and clone proof are both slow-tier pre-push checks.

Historical hardcodes are scanned in the same reachable objects and reported in
`publication-hardcode-history.json` for the SWX-12 visibility decision. A clean
tracked tree does not assert clean historical topology; resolve or explicitly
review every historical finding before publication. Secret scanning recognizes
provider/Bearer keys, encrypted and PGP private keys, UTF-16 text, and bounded
ZIP/wheel, gzip and tar archives. Malformed or oversized archives fail closed.
History objects stream through one Git batch process. Licence markers cover
Linux/macOS and Python 3.11–3.13; any selected dependency lacking installed
metadata fails closed rather than being silently excluded by the current host.

Every existing supported CI combination runs its one suite from a freshly
installed clone through the network-denying offline wrapper. The wheel cache is
prepared once inside that clone. Only the Ubuntu 3.12 publication lane also runs
the dedicated 168-cell battery and the three publication gates. macOS retains
the existing weekly/on-demand schedule; no per-PR macOS lane is added.

## Reports, leaderboard and site

Published numbers live only in `reports/<date>-<bench>/summary.json`. After adding or
correcting a report, run:

```sh
python3 scripts/check_reports.py --write   # render REPORT.md and run the report gates
python3 scripts/build_site.py              # regenerate site/: overview, results, methodology, infographic
```

CI fails if `site/` is stale. Never edit `site/` by hand. Corrections are published as dated
notes or new reports beside the original numbers; never silently overwrite a published figure.

## For maintainers of tested services

If a configuration here misrepresents your service, open an issue with:
- the configuration you recommend (server version, enabled tools, settings);
- the affected report and table row.

Configuration fixes come with a test and a re-run of the affected arms, published as a new
report. Grading disputes are welcome too: cite the cell and the rubric item.
