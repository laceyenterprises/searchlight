# Running a sponsored insight study

This kit is for anyone, a search vendor included, who wants to run or sponsor an INS
study: the same questions posed to every search and research API, scored on verified
insight that no other API found. The method is in the [bench README](README.md), and the
vendor evidence behind every profile is in [provider best practices](provider-best-practices.md).

```
define the set ─► choose arms ─► dry run ─► run ─► import external arms ─► grade ─► report ─► publish
```

## 1. Define the question set

Write `catalogs/insight/<set_id>.json`. The `set_id` ends in a date, and the set is frozen once it
runs: a new version is a new file, so comparable runs stay comparable.

```json
{
  "set_id": "agent-memory-2027-01",
  "title": "How production agents store and reuse memory",
  "frozen_on": "2027-01-10",
  "context_for_judges": "Who reads the findings, what they already run, and what kind of fact would change their decision.",
  "baseline": "agent-memory-2027-01.baseline.md",
  "sponsor": {"name": "Acme Search", "products": ["acme"], "disclosure": "Acme paid for API credits and grading time; it did not see results before publication."},
  "questions": [{"id": "Q01", "text": "...", "freshness_days": null}]
}
```

- **Questions** go to every arm verbatim. Write the question a reader would ask, not a query tuned to
  one engine. `freshness_days` dates a recency window from the run date.
- **`context_for_judges`** is the only thing the value judge knows about the reader. It decides what
  counts as decision-changing (3), new design detail (2), corroborating (1) or generic (0).
- **`baseline`** (optional) is what the reader already knows. Findings it already states score 0.
- **`sponsor`** (optional) needs a name, a disclosure and the vendor arms that are the sponsor's own
  `products`. The disclosure is printed at the top of every report and dashboard. The sponsor's arms
  are tagged in every table.

## 2. Choose and add arms

Default arms are every profile in `config/insight/profiles/`, one per vendor and operation: a search
arm and the vendor's own research arm. Pass `--arms` to choose arm ids, or vendors to get every
default profile for that vendor.

- **A new arm** is a new profile file; the README's "Add or change an arm" section lists every field.
  Each field takes its value from the vendor's own documentation, with a docs link and a rationale.
  Anything not confirmed goes in `unverified_fields`.
- **A sponsor-requested arm** is a profile with `"requested_by": "<sponsor name>"`. A sponsor may
  request arms for its own `products` only. The run refuses a sponsor-requested profile for any
  other vendor.
- **Variants** (`<arm>.<operation>.<variant>.json`, for example `exa.research.xhigh`) are opt-in. They
  must be named before the run, and they are reported beside their primary arm, never instead of it.
- **The sponsor cannot tune other vendors' profiles.**
  - A sponsor may file a correction to another vendor's profile, but it lands only with that
    vendor's docs as evidence, and it changes the profile's hash.
  - A run never mixes profile hashes, so a corrected profile starts a new run.

## 3. Imported arms (harness-native and other external arms)

Some arms cannot run through a vendor API, for example a coding agent's own WebSearch and WebFetch,
or a vendor demo run by the vendor. Those arms join a run with `sew insight import`:

```sh
sew insight import --run RUN --arm claude-native.agent --claims claims.jsonl \
  --label "Claude Code native WebSearch/WebFetch" --usd 4.10 \
  --note "freeform research brief mapped to questions afterwards; not posed each question verbatim"
```

- **Input.** `claims.jsonl` holds one `{qid, claim, source_url, retrieved_via}` per line.
- **Same grading.** The run's own extractor re-reads the claims, and each claim is verified on its
  own page like every other arm's. Pre-split claims are never trusted as they are.
- **Caveat.** `--note` is the arm's caveat. It is printed wherever the arm appears, and the arm is
  always marked `imported`.
- **Cost.** `--usd` records reported spend. Without it, the arm's cost is shown as unknown, not as zero.
- **Refusals.** An arm id that collides with a profile is refused, and so is a question that is not
  in the set.

## 4. Budget and caps

```sh
sew insight run --set SET --extractor-model MODEL --dry-run
```

- **The dry run** prints every planned cell, its estimate, the per-arm caps, the arms that cannot run
  here, and every unverified profile field. It makes no calls and writes nothing.
- **The run cap.** `--max-usd` is a hard cap, and a run whose estimate exceeds it is refused before
  any call. `--arm-max-usd` caps each arm; the default is twice the arm's estimate.
- **The ledger.** It reserves each cell's estimate before the call, and records measured spend after
  it. A cell that would cross a cap is recorded as `skipped_cap`.
- **Smoke first.** Start with `--questions Q01` across every arm. It confirms the unverified fields
  for a few dollars.

## 5. Run, grade, report, publish

```sh
SEW_HARNESS_LIVE=1 sew insight run --set SET --extractor-model MODEL --max-usd N   # resumable, same day
sew insight import --run RUN --arm ...                                             # optional
SEW_HARNESS_LIVE=1 sew insight grade --run RUN --judge-model MODEL                 # resumable
sew insight report --run RUN                                                       # offline, free
sew insight publish --run RUN --to reports/YYYY-MM-DD-SLUG/
```

- **`report`** writes `<run>/report/` with three files:
  - `summary.json`, versioned and validated against `config/insight/summary.schema.json`;
  - `REPORT.md`;
  - `dashboard.html`, a single self-contained page with a leaderboard, the unique insights, the
    overlap, best-k, cost against unique insight, a per-question drill-down, claim search and
    every raw file.

  Every metric and every claim links to the raw files it came from. Open the dashboard straight
  from disk.
- **`publish`** packages a shareable study:
  - the report, the set, the frozen profiles, generated `methodology.md` and `reproduce.md`, and
    sanitized data;
  - a link from `reports/README.md`.

  Captured pages and full vendor answers never leave the machine that ran the study: result text is
  cut to 300-character excerpts, and links to the full files read *local only*. Credentials are
  never written anywhere.

## 6. Fairness and disclosure rules

1. **Same day, same questions, same budgets.**
   - Every arm gets every question verbatim on one UTC day.
   - Research arms all get one shared research brief.
   - Search arms get the same top-k and text budgets.
2. **Every vendor at its documented best.** Profiles follow each vendor's own docs, and no vendor
   gets hand-tuned queries, operators or domain lists. The one shared query-writer feeds only the
   vendors whose docs prescribe it.
3. **Frozen before results.** The set, the shared templates, every profile and the extractor are
   hashed into the run. A change starts a new run, and nothing is retuned after results are seen.
4. **Strict, blind grading.**
   - Credit needs the arm's own cited page to support the claim.
   - Judges never see arm names, URLs or vendor brands, and they see findings in a seeded shuffle.
5. **Disclosure.**
   - A sponsor, its products and its disclosure appear at the top of every report.
   - Imported arms are marked and carry their caveat.
   - Unverified profile fields are listed.
   - Every figure is labeled with its n and interval.
6. **Corrections are published beside the original numbers, never in place of them.**
