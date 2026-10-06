"""Offline tests for the WSB production task catalog and its scorer.

Three groups of tests, and the middle one is the load-bearing group.

*Structure* asserts the properties the suite is unusable without: 18 tasks,
three per class, one scoring instrument per task, budgets present and bounded,
freshness windows that are internally consistent, and enough expected-fail
tasks spread across enough classes to keep measurement headroom. Each is paired
with a mutation test that breaks the property and asserts the loader refuses,
because a guard that has never been seen to fire is not a guard.

*Satisfiability* checks every deterministically scored task against a worked
model answer. This is the test that catches the failure mode a reviewer cannot
see by reading YAML: a regex typo, a host allowlist that excludes its own
required host, a member floor above the achievable count. Any of those makes
every arm fail for reasons that have nothing to do with the arm, and in the
report it is indistinguishable from a hard task.

*Scoring* pins golden records, in both directions. A golden that only pins a
pass would be satisfied by a scorer that always passes.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from conftest import CATALOG_ROOT

from sew.production_catalog import (
    MIN_EXPECTED_FAIL_CLASSES,
    MIN_EXPECTED_FAIL_TASKS,
    TASKS_PER_CLASS,
    load_production_catalog,
    publication_blockers,
    validate_production_catalog,
)
from sew.production_scoring import score_deliverable, suspect_ground_truth_fields
from sew.schema import SchemaError

GOLDEN_ROOT = CATALOG_ROOT / "fixtures" / "production" / "golden"
MODEL_ANSWER_ROOT = CATALOG_ROOT / "fixtures" / "production" / "model-answers"
GOLDEN_CASES = (
    ("python313-removals.complete", "list-build-python313-pep594-removals"),
    ("python313-removals.superset", "list-build-python313-pep594-removals"),
    ("postgres-guc.honest", "unanswerable-nonexistent-postgres-guc"),
    ("postgres-guc.fabricated", "unanswerable-nonexistent-postgres-guc"),
)


@pytest.fixture(scope="module")
def catalog() -> dict:
    return load_production_catalog(CATALOG_ROOT)["catalog"]


@pytest.fixture(scope="module")
def tasks_by_id(catalog: dict) -> dict:
    return {task["id"]: task for task in catalog["tasks"]}


def _copy_catalog(tmp_path: Path) -> Path:
    source = CATALOG_ROOT / "catalogs" / "production"
    target = tmp_path / "catalogs" / "production"
    target.mkdir(parents=True)
    for name in ("tasks.yaml", "rubrics.yaml"):
        (target / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    return target


def _mutate_tasks(tmp_path: Path, old: str, new: str, count: int = 1) -> Path:
    target = _copy_catalog(tmp_path)
    path = target / "tasks.yaml"
    text = path.read_text(encoding="utf-8")
    assert text.count(old) >= count, f"fixture no longer contains {old!r}"
    path.write_text(text.replace(old, new, count), encoding="utf-8")
    return tmp_path


def _mutate_document(tmp_path: Path, mutate) -> Path:
    """Mutate the catalog as a parsed document, for edits a text replace cannot
    express safely (removing a nested block, rewriting one field in place)."""

    target = _copy_catalog(tmp_path)
    path = target / "tasks.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return tmp_path


def _model_answer(task_id: str) -> dict:
    """A fresh mutable copy, so a mutating test cannot leak into another."""

    return json.loads((MODEL_ANSWER_ROOT / f"{task_id}.json").read_text())


def _deterministic_tasks(catalog: dict) -> list[dict]:
    return [task for task in catalog["tasks"] if "deterministic_validator" in task]


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------


def test_catalog_validates_offline() -> None:
    task_ids = validate_production_catalog(CATALOG_ROOT)

    assert len(task_ids) == 18
    assert len(set(task_ids)) == 18


def test_every_class_carries_its_quota(catalog: dict) -> None:
    counts: dict[str, int] = {}
    for task in catalog["tasks"]:
        counts[task["task_class"]] = counts.get(task["task_class"], 0) + 1

    assert counts == {
        "list_build": TASKS_PER_CLASS,
        "change_detection": TASKS_PER_CLASS,
        "upstream_diagnosis": TASKS_PER_CLASS,
        "competitive_table": TASKS_PER_CLASS,
        "multi_hop_entity": TASKS_PER_CLASS,
        "unanswerable": TASKS_PER_CLASS,
    }


def test_every_task_declares_exactly_one_scoring_instrument(catalog: dict) -> None:
    for task in catalog["tasks"]:
        instruments = [key for key in ("deterministic_validator", "judge_rubric") if key in task]
        assert len(instruments) == 1, f"{task['id']} declares {instruments}"


def test_catalog_rejects_task_with_neither_validator_nor_rubric(tmp_path: Path) -> None:
    def drop_validator(document: dict) -> None:
        for task in document["tasks"]:
            if task.pop("deterministic_validator", None) is not None:
                return

    root = _mutate_document(tmp_path, drop_validator)

    with pytest.raises(SchemaError, match="never neither"):
        validate_production_catalog(root)


def test_catalog_rejects_task_with_both_validator_and_rubric(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "    judge_rubric:\n      rubric_id: wsb01-list-build-v1\n      minimum_score: 4",
        "    judge_rubric:\n      rubric_id: wsb01-list-build-v1\n      minimum_score: 4\n"
        "    deterministic_validator:\n      kind: entity_set\n      fields:\n"
        "        - {name: evidence_urls, check: citation_set, min_citations: 1}",
    )

    with pytest.raises(SchemaError, match="never both"):
        validate_production_catalog(root)


def test_every_task_declares_positive_bounded_budgets(catalog: dict) -> None:
    ceilings = catalog["budget_ceilings"]

    for task in catalog["tasks"]:
        budgets = task["budgets"]
        assert set(budgets) == set(ceilings), task["id"]
        for key, ceiling in ceilings.items():
            assert isinstance(budgets[key], int), f"{task['id']}.{key}"
            assert budgets[key] > 0, f"{task['id']}.{key}"
            assert budgets[key] <= ceiling, f"{task['id']}.{key}"


@pytest.mark.parametrize("bad_value", ["0", "-1"])
def test_catalog_rejects_non_positive_budget(tmp_path: Path, bad_value: str) -> None:
    root = _mutate_tasks(
        tmp_path,
        "budgets: {max_provider_calls: 40,",
        f"budgets: {{max_provider_calls: {bad_value},",
    )

    with pytest.raises(SchemaError, match="max_provider_calls must be a positive integer"):
        validate_production_catalog(root)


def test_catalog_rejects_budget_above_the_suite_ceiling(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "budgets: {max_provider_calls: 40,",
        "budgets: {max_provider_calls: 61,",
    )

    with pytest.raises(SchemaError, match="exceeds the suite ceiling"):
        validate_production_catalog(root)


def test_catalog_rejects_missing_budget_field(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "budgets: {max_provider_calls: 40, max_wall_clock_seconds: 900, max_total_tokens: 300000}",
        "budgets: {max_provider_calls: 40, max_wall_clock_seconds: 900}",
    )

    with pytest.raises(SchemaError, match="max_total_tokens must be a positive integer"):
        validate_production_catalog(root)


def test_catalog_has_expected_fail_headroom(catalog: dict) -> None:
    expected_fail = [
        task for task in catalog["tasks"] if task["expected_outcome"] == "expected_fail"
    ]

    assert len(expected_fail) >= MIN_EXPECTED_FAIL_TASKS
    assert len({task["task_class"] for task in expected_fail}) >= MIN_EXPECTED_FAIL_CLASSES
    for task in expected_fail:
        assert task["expected_fail_reason"].strip(), task["id"]


def test_catalog_rejects_losing_its_expected_fail_headroom(tmp_path: Path) -> None:
    def clear_headroom(document: dict) -> None:
        for task in document["tasks"]:
            if task["expected_outcome"] == "expected_fail":
                task["expected_outcome"] = "competitive"
                task.pop("expected_fail_reason", None)

    root = _mutate_document(tmp_path, clear_headroom)

    with pytest.raises(SchemaError, match="at least 3 expected-fail tasks"):
        validate_production_catalog(root)


def test_catalog_rejects_headroom_parked_in_too_few_classes(tmp_path: Path) -> None:
    # Three expected-fail tasks all inside one class is not headroom: the suite
    # reports per class, so every other class stays free to saturate.
    target = _copy_catalog(tmp_path)
    path = target / "tasks.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    for task in document["tasks"]:
        if task["task_class"] == "unanswerable":
            task["expected_outcome"] = "expected_fail"
            task["expected_fail_reason"] = "test mutation"
        elif task["expected_outcome"] == "expected_fail":
            task["expected_outcome"] = "competitive"
            task.pop("expected_fail_reason", None)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(SchemaError, match="spread across at least"):
        validate_production_catalog(tmp_path)


def test_catalog_rejects_expected_fail_without_a_reason(tmp_path: Path) -> None:
    def blank_reason(document: dict) -> None:
        for task in document["tasks"]:
            if task["expected_outcome"] == "expected_fail":
                task["expected_fail_reason"] = "   "
                return

    root = _mutate_document(tmp_path, blank_reason)

    with pytest.raises(SchemaError, match="expected_fail_reason must be a non-empty string"):
        validate_production_catalog(root)


def test_every_task_declares_a_consistent_freshness_window(catalog: dict) -> None:
    for task in catalog["tasks"]:
        freshness = task["freshness"]
        as_of = date.fromisoformat(freshness["as_of"])
        revalidate_by = date.fromisoformat(freshness["revalidate_by"])
        assert freshness["window_days"] > 0, task["id"]
        assert (revalidate_by - as_of).days == freshness["window_days"], task["id"]
        assert freshness["drift_risk"] in {"low", "medium", "high"}, task["id"]


def test_catalog_rejects_a_window_that_does_not_match_its_deadline(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        'window_days: 730, revalidate_by: "2028-09-20"',
        'window_days: 730, revalidate_by: "2030-09-20"',
    )

    with pytest.raises(SchemaError, match="revalidate_by must equal as_of plus window_days"):
        validate_production_catalog(root)


def test_no_task_is_satisfiable_by_one_scalar_pattern(catalog: dict) -> None:
    # The L2 catalog saturated because every answer was one regex-matchable
    # token. Structurally: every deliverable requires several fields, one of
    # them carries citations, and at least one check is non-scalar.
    for task in catalog["tasks"]:
        required = task["deliverable_schema"]["required"]
        assert len(required) >= 2, task["id"]
        assert task["evidence_field"] in required, task["id"]

    for task in _deterministic_tasks(catalog):
        checks = {field["check"] for field in task["deterministic_validator"]["fields"]}
        assert checks & {"member_set", "citation_set", "table_cells", "decline"}, task["id"]


def test_catalog_rejects_a_purely_scalar_validator(tmp_path: Path) -> None:
    # This task's only structural check is its citation set; downgrading it to
    # a scalar match leaves nothing but pattern matches on single values.
    def flatten_checks(document: dict) -> None:
        for task in document["tasks"]:
            if task["id"] != "upstream-diagnosis-node-openssl3-md4":
                continue
            for field in task["deterministic_validator"]["fields"]:
                if field["check"] == "citation_set":
                    field.clear()
                    field.update(
                        {"name": "evidence_urls", "check": "value_match", "any_of": ["https://"]}
                    )

    root = _mutate_document(tmp_path, flatten_checks)

    with pytest.raises(SchemaError, match="single regex-matchable token"):
        validate_production_catalog(root)


def test_catalog_rejects_a_required_field_nothing_scores(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "        - name: module_count\n          check: value_match\n"
        "          any_of: ['^\\s*19\\s*$', '\\b19\\b']\n",
        "",
    )

    with pytest.raises(SchemaError, match="unscored"):
        validate_production_catalog(root)


def test_catalog_rejects_a_validator_kind_from_another_class(tmp_path: Path) -> None:
    root = _mutate_tasks(tmp_path, "kind: supported_decline", "kind: entity_set")

    with pytest.raises(SchemaError, match="validator kind must be one of"):
        validate_production_catalog(root)


def test_catalog_rejects_an_unknown_rubric_id(tmp_path: Path) -> None:
    root = _mutate_tasks(tmp_path, "rubric_id: wsb01-list-build-v1", "rubric_id: wsb01-missing-v1")

    with pytest.raises(SchemaError, match="unknown judge_rubric.rubric_id"):
        validate_production_catalog(root)


def test_catalog_rejects_an_unknown_task_key(tmp_path: Path) -> None:
    root = _mutate_tasks(tmp_path, "    task_class: list_build", "    task_klass: list_build")

    with pytest.raises(SchemaError, match="unknown key"):
        validate_production_catalog(root)


def test_catalog_rejects_an_invalid_ground_truth_regex(tmp_path: Path) -> None:
    root = _mutate_tasks(tmp_path, "'\\baifc\\b'", "'\\baifc(\\b'")

    with pytest.raises(SchemaError, match="invalid regex"):
        validate_production_catalog(root)


def test_catalog_rejects_a_required_host_outside_its_own_allowlist(tmp_path: Path) -> None:
    # The subtree reading of `allowed_hosts` is directional: requiring
    # `python.org` while allowing only `docs.python.org` still names a host the
    # allowlist does not contain, and the author almost certainly meant to
    # require the subdomain. The other direction is accepted -- see
    # `test_catalog_accepts_a_required_subdomain_under_an_allowed_parent`.
    root = _mutate_tasks(
        tmp_path,
        "          min_distinct_hosts: 1\n          required_hosts: [python.org]",
        "          min_distinct_hosts: 1\n          required_hosts: [python.org]\n"
        "          allowed_hosts: [docs.python.org]",
    )

    with pytest.raises(SchemaError, match="allowlist excludes"):
        validate_production_catalog(root)


def test_catalog_rejects_pinned_truth_without_provenance(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "      status: immutable_history\n      verification_method: author_assertion",
        "      status: immutable_history\n      verification_method: hunch",
    )

    with pytest.raises(SchemaError, match="verification_method"):
        validate_production_catalog(root)


def test_catalog_rejects_live_truth_without_a_note(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "      note: >-\n        Published cloud egress prices change without notice.",
        "      unused_note: >-\n        Published cloud egress prices change without notice.",
    )

    with pytest.raises(SchemaError, match="unknown key"):
        validate_production_catalog(root)


def test_rubric_scored_task_cannot_claim_pinned_truth(tmp_path: Path) -> None:
    root = _mutate_tasks(
        tmp_path,
        "      status: none\n      note: >-\n        Open selection criteria",
        "      status: immutable_history\n      note: >-\n        Open selection criteria",
    )

    with pytest.raises(SchemaError, match="rubric-scored"):
        validate_production_catalog(root)


def test_rubrics_declare_multiple_dimensions_and_blinding() -> None:
    rubrics = load_production_catalog(CATALOG_ROOT)["rubrics"]["rubrics"]
    referenced = {
        task["judge_rubric"]["rubric_id"]
        for task in load_production_catalog(CATALOG_ROOT)["catalog"]["tasks"]
        if "judge_rubric" in task
    }

    assert referenced <= {rubric["rubric_id"] for rubric in rubrics}
    for rubric in rubrics:
        # A one-dimension rubric is a holistic score, which hides which
        # dimension an arm failed -- the only thing `explain` can show.
        assert len(rubric["dimensions"]) >= 2, rubric["rubric_id"]
        assert "strip" in rubric["blinding_note"] or "stripped" in rubric["blinding_note"]


# --------------------------------------------------------------------------
# Satisfiability
# --------------------------------------------------------------------------


def test_every_deterministic_task_is_satisfiable(catalog: dict) -> None:
    unsatisfiable: dict[str, dict] = {}
    for task in _deterministic_tasks(catalog):
        payload = _model_answer(task["id"])
        record = score_deliverable(task, payload)
        if not record["passed"]:
            unsatisfiable[task["id"]] = {
                name: detail["reasons"]
                for name, detail in record["field_detail"].items()
                if detail["correct"] is False
            }

    # A task no deliverable can satisfy fails every arm for a reason that has
    # nothing to do with the arm, and reads in the report as a hard task.
    assert unsatisfiable == {}


def test_model_answers_cover_exactly_the_deterministic_tasks(catalog: dict) -> None:
    on_disk = {path.stem for path in MODEL_ANSWER_ROOT.glob("*.json")}

    assert on_disk == {task["id"] for task in _deterministic_tasks(catalog)}


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("stem", "task_id"), GOLDEN_CASES)
def test_golden_fixture_scores_deterministically(
    tasks_by_id: dict, stem: str, task_id: str
) -> None:
    deliverable = json.loads((GOLDEN_ROOT / f"{stem}.deliverable.json").read_text())
    expected = json.loads((GOLDEN_ROOT / f"{stem}.score.json").read_text())

    first = score_deliverable(tasks_by_id[task_id], deliverable)
    second = score_deliverable(tasks_by_id[task_id], deliverable)

    assert first == expected
    assert first == second


def test_golden_fixtures_pin_both_a_pass_and_a_failure() -> None:
    verdicts = {
        stem: json.loads((GOLDEN_ROOT / f"{stem}.score.json").read_text())["passed"]
        for stem, _ in GOLDEN_CASES
    }

    # A golden set that only pins passes is satisfied by a scorer that always
    # passes, which is the failure mode a golden is supposed to catch.
    assert True in verdicts.values()
    assert False in verdicts.values()


def test_scoring_ignores_key_order(tasks_by_id: dict) -> None:
    deliverable = json.loads(
        (GOLDEN_ROOT / "python313-removals.complete.deliverable.json").read_text()
    )
    reversed_keys = dict(reversed(list(deliverable.items())))
    task = tasks_by_id["list-build-python313-pep594-removals"]

    assert score_deliverable(task, deliverable) == score_deliverable(task, reversed_keys)


def test_missing_field_is_a_decline_and_wrong_field_is_a_fabrication(
    tasks_by_id: dict,
) -> None:
    task = tasks_by_id["upstream-diagnosis-unindexed-ordering-regression"]
    declined = {
        "first_release_containing": "3.4.0",
        "reasoning": "x" * 200,
        "evidence_urls": [
            "https://github.com/o/r/commit/4f3c1a9e2b7d8c6f5a4b3c2d1e0f9a8b7c6d5e4f",
            "https://github.com/o/r/blob/4f3c1a9e2b7d8c6f5a4b3c2d1e0f9a8b7c6d5e4f/a.js",
        ],
    }
    fabricated = dict(declined, introducing_commit_url=["https://example.invalid/guess"])

    declined_record = score_deliverable(task, declined)
    fabricated_record = score_deliverable(task, fabricated)

    # Both score zero on the field and they are not the same finding: one arm
    # said it could not establish the commit, the other produced one it cannot
    # support.
    assert declined_record["field_detail"]["introducing_commit_url"]["state"] == "missing"
    assert fabricated_record["field_detail"]["introducing_commit_url"]["state"] == "wrong"
    assert declined_record["passed"] is False
    assert fabricated_record["passed"] is False


def test_unavailable_cell_passes_and_a_guessed_figure_does_not(tasks_by_id: dict) -> None:
    task = tasks_by_id["competitive-table-unpublished-enterprise-pricing"]
    honest = _model_answer("competitive-table-unpublished-enterprise-pricing")
    guessed = _model_answer("competitive-table-unpublished-enterprise-pricing")
    guessed["rows"][0]["list_price_per_seat_usd"] = "15"

    assert score_deliverable(task, honest)["passed"] is True
    record = score_deliverable(task, guessed)
    assert record["passed"] is False
    assert any("not published" in reason for reason in record["field_detail"]["rows"]["reasons"])


def test_a_url_cell_cannot_be_marked_unavailable(tasks_by_id: dict) -> None:
    task = tasks_by_id["competitive-table-unpublished-enterprise-pricing"]
    payload = _model_answer("competitive-table-unpublished-enterprise-pricing")
    payload["rows"][0]["source_url"] = "unavailable"

    record = score_deliverable(task, payload)

    # The token stands in for a figure the vendor withheld. The citation for
    # the page that withheld it is still required, so the token must not
    # shortcut the URL rule.
    assert record["passed"] is False
    assert any("https" in reason for reason in record["field_detail"]["rows"]["reasons"])


def test_no_cell_value_opts_out_of_the_rules_that_name_it(tasks_by_id: dict) -> None:
    """A pinned cell stays pinned no matter what string the arm parks in it.

    The AWS free egress allowance is published and the catalog pins it to 100.
    An arm that answers "unavailable" there is not declining a figure nobody
    publishes -- it is getting a knowable figure wrong -- so the cell rule has
    to fire. This regressed once: an `unavailable` token short-circuited the
    cell loop, so one magic string satisfied every rule that named the cell
    and the pinned ground truth scored correct.
    """

    task = tasks_by_id["competitive-table-cloud-egress-pricing"]
    payload = _model_answer("competitive-table-cloud-egress-pricing")
    assert score_deliverable(task, payload)["passed"] is True

    aws = next(row for row in payload["rows"] if "aws" in str(row["provider"]).lower())
    aws["free_monthly_egress"] = "unavailable"

    record = score_deliverable(task, payload)

    assert record["passed"] is False
    assert any(
        "free_monthly_egress" in reason for reason in record["field_detail"]["rows"]["reasons"]
    )


def test_a_column_no_rule_names_still_accepts_an_unpublished_figure(tasks_by_id: dict) -> None:
    """The permissive case needs no token: an unruled column takes any answer.

    GCP's per-GB rate is not pinned by the catalog -- published cloud prices
    drift -- so an arm may honestly report it as unavailable. Removing the
    opt-out token must not have cost that, or the table would demand a figure
    the catalog itself declines to fix.
    """

    task = tasks_by_id["competitive-table-cloud-egress-pricing"]
    payload = _model_answer("competitive-table-cloud-egress-pricing")

    gcp = next(row for row in payload["rows"] if "google" in str(row["provider"]).lower())
    gcp["first_tier_per_gb_usd"] = "unavailable"

    assert score_deliverable(task, payload)["passed"] is True


def test_a_closed_count_brief_caps_as_well_as_floors(tasks_by_id: dict) -> None:
    task = tasks_by_id["multi-hop-npm-semver-dependents"]
    payload = _model_answer("multi-hop-npm-semver-dependents")
    entries = payload["ranked_dependents"]
    entries.append(dict(entries[0], package="example-extra"))

    record = score_deliverable(task, payload)

    # The brief asks for five. `allow_extra: false` with no named members has
    # to bind as a cap too, or a six-entry answer scores as a five-entry one.
    assert record["passed"] is False
    assert any(
        "at most 5" in reason for reason in record["field_detail"]["ranked_dependents"]["reasons"]
    )


def test_a_duplicated_entity_cannot_pass_on_its_good_row(tasks_by_id: dict) -> None:
    task = tasks_by_id["multi-hop-cve-to-fixed-release"]
    payload = _model_answer("multi-hop-cve-to-fixed-release")
    findings = payload["findings"]
    findings.append(dict(findings[0], first_fixed_release="2.16.0"))

    record = score_deliverable(task, payload)

    # Per-entity rules apply to every element matching the entity, so listing
    # it twice with one wrong release cannot pass on the correct row.
    assert record["passed"] is False
    assert any(
        "first_fixed_release" in reason for reason in record["field_detail"]["findings"]["reasons"]
    )


def test_rubric_scored_task_defers_rather_than_inventing_a_score(tasks_by_id: dict) -> None:
    record = score_deliverable(tasks_by_id["list-build-sca-tool-shortlist"], {"candidates": []})

    # WSB-02 owns the judge. A fallback number here would let a judged task
    # look scored when nothing judged it.
    assert record["scoring"] == "rubric_deferred"
    assert record["passed"] is None
    assert record["rubric_id"] == "wsb01-list-build-v1"


# --------------------------------------------------------------------------
# Suspect ground truth and publication readiness
# --------------------------------------------------------------------------


def test_two_arms_failing_a_field_flags_suspect_ground_truth(catalog: dict) -> None:
    records = [
        {
            "task_id": "list-build-python313-pep594-removals",
            "arm": arm,
            "field_detail": {
                "module_count": {"correct": False},
                "evidence_urls": {"correct": True},
            },
        }
        for arm in ("claude-code+exa", "claude-code+native")
    ]

    assert suspect_ground_truth_fields(records, catalog) == [
        "list-build-python313-pep594-removals.module_count"
    ]


def test_one_arm_repeated_is_not_multi_arm_agreement(catalog: dict) -> None:
    records = [
        {
            "task_id": "list-build-python313-pep594-removals",
            "arm": "claude-code+exa",
            "field_detail": {"module_count": {"correct": False}},
        }
        for _ in range(5)
    ]

    # Repetitions are not independent votes. One noisy arm run five times must
    # not look like two arms agreeing.
    assert suspect_ground_truth_fields(records, catalog) == []


def test_expected_fail_tasks_are_exempt_from_the_suspect_heuristic(catalog: dict) -> None:
    records = [
        {
            "task_id": "competitive-table-unpublished-enterprise-pricing",
            "arm": arm,
            "field_detail": {"rows": {"correct": False}},
        }
        for arm in ("claude-code+exa", "claude-code+firecrawl", "claude-code+native")
    ]

    # Universal failure here is the declared design. Flagging it as suspect
    # truth would delete the catalog's measurement headroom by reclassifying
    # it as an authoring error.
    assert suspect_ground_truth_fields(records, catalog) == []


def test_publication_blockers_report_unverified_and_stale_truth(catalog: dict) -> None:
    blockers = publication_blockers(catalog, today=date(2026, 9, 21))
    reasons = {blocker["reason"] for blocker in blockers}

    assert reasons == {"truth_unverified"}
    assert all(blocker["task_id"] for blocker in blockers)

    # Every live-condition task ages out of its own window eventually, and the
    # catalog is what says when.
    later = publication_blockers(catalog, today=date(2027, 1, 1))
    assert "freshness_window_expired" in {blocker["reason"] for blocker in later}


# --- malformed citations must fail a field, never the run --------------------

# Verified empirically to raise ValueError from `urlsplit(...).hostname` /
# `.path` on this interpreter. An out-of-range PORT does not belong here: it
# raises only when `.port` is read, which neither helper does.
MALFORMED_URLS = (
    "https://[::1",  # unclosed IPv6 bracket
    "http://[v1.fe80::a",  # unclosed IPvFuture bracket
    "https://[:::]",  # bracketed, but not an address
    "http://]bad[",  # brackets inverted
)


@pytest.mark.parametrize("bad_url", MALFORMED_URLS)
def test_a_malformed_citation_fails_its_field_without_crashing(
    tasks_by_id: dict, bad_url: str
) -> None:
    """Every citation string is untrusted arm output.

    `urlsplit` raises ValueError on inputs a model will plausibly emit, and
    this runs inside the offline scoring loop. Letting it escape means one
    malformed citation from one arm takes down the whole benchmark run and the
    partial results are unscored -- an availability bug masquerading as a
    scoring bug.
    """
    task_id = "list-build-python313-pep594-removals"
    task = tasks_by_id[task_id]
    answer = _model_answer(task_id)
    answer["evidence_urls"] = [bad_url]

    record = score_deliverable(task, answer)

    assert record["passed"] is False
    reasons = record["field_detail"]["evidence_urls"]["reasons"]
    assert any("not a parseable URL" in reason for reason in reasons), reasons


def test_host_and_path_helpers_degrade_instead_of_raising() -> None:
    # Both call sites take the same untrusted string; the reviewer found one.
    from sew.production_scoring import _host_of, _path_of

    for bad_url in MALFORMED_URLS:
        assert _host_of(bad_url) == ""
        assert _path_of(bad_url) == ""

    # A well-formed URL is unaffected.
    good = "https://github.com/o/r/blob/abc/a.js"
    assert _host_of(good) == "github.com"
    assert _path_of(good) == "/o/r/blob/abc/a.js"


# --- a rule named for a child field is a claim about that child field --------


def test_a_flat_member_list_cannot_satisfy_child_field_rules(tasks_by_id: dict) -> None:
    """`element_rules_all` names a child field, so a scalar member has none.

    The brief asks for five entries each carrying a package, a maintainer, and
    a download count. A flat list of package names carries none of those keys,
    but every name is a non-empty string -- so scoring the rules against the
    whole element would pass `package` and `maintainer` at once for a
    deliverable that answered neither, which is precisely the structural
    failure these checks exist to catch.
    """
    task_id = "multi-hop-npm-semver-dependents"
    task = tasks_by_id[task_id]
    answer = _model_answer(task_id)
    # The shape an arm produces when it answers the brief as prose bullets
    # rather than as the declared objects.
    answer["ranked_dependents"] = [
        " ".join(str(value) for value in entry.values()) for entry in answer["ranked_dependents"]
    ]

    record = score_deliverable(task, answer)

    assert record["passed"] is False
    reasons = record["field_detail"]["ranked_dependents"]["reasons"]
    assert any("field package is empty" in reason for reason in reasons), reasons
    assert any("field maintainer is empty" in reason for reason in reasons), reasons


def test_a_flat_member_list_still_fails_an_https_child_rule(tasks_by_id: dict) -> None:
    # Same defect on the other kind of child rule: `source_url` must be an
    # https URL, and a member that carries no `source_url` key must not pass
    # merely because the element stringifies to something containing one.
    task_id = "list-build-python313-pep594-removals"
    task = tasks_by_id[task_id]
    answer = _model_answer(task_id)
    answer["removed_modules"] = [
        f"{entry['module']} {entry['source_url']}" for entry in answer["removed_modules"]
    ]

    record = score_deliverable(task, answer)

    assert record["passed"] is False
    reasons = record["field_detail"]["removed_modules"]["reasons"]
    assert any("field source_url is empty" in reason for reason in reasons), reasons


@pytest.mark.parametrize("deliverable", [[], ["a", "b"], "a string", 42, None, True])
def test_a_non_mapping_deliverable_scores_as_missing_rather_than_crashing(
    tasks_by_id: dict, deliverable: object
) -> None:
    # An arm may emit a JSON array or a scalar where the schema asks for an
    # object. `score_deliverable` normalises a non-mapping deliverable to an
    # empty mapping precisely so one such arm fails its own fields instead of
    # taking the batch down; this pins that guard.
    record = score_deliverable(tasks_by_id["multi-hop-npm-semver-dependents"], deliverable)

    assert record["passed"] is False
    assert record["schema_valid"] is False
    assert {field["state"] for field in record["field_detail"].values()} == {"missing"}


# --- the preflight must predict the scorer, not out-strict it ----------------


def test_catalog_accepts_a_required_subdomain_under_an_allowed_parent(tmp_path: Path) -> None:
    """`allowed_hosts` declares subtrees, so the preflight must read subtrees.

    The scorer matches a cited host against `allowed_hosts` with
    `_host_matches`, so a citation on `docs.python.org` is inside an allowlist
    that declares `python.org`. A preflight that compared the two as literal
    strings refused that catalog before it ever ran -- rejecting a correct
    catalog for a property it does not violate, which is the same class of
    false alarm as a guard that never fires, just louder.
    """
    root = _mutate_tasks(
        tmp_path,
        "          min_distinct_hosts: 1\n          required_hosts: [python.org]",
        "          min_distinct_hosts: 1\n          required_hosts: [docs.python.org]\n"
        "          allowed_hosts: [python.org]",
    )

    validate_production_catalog(root)


# --- prose in a citation field is not a citation -----------------------------


def test_prose_in_an_unconstrained_citation_field_is_refused(tasks_by_id: dict) -> None:
    """The weakest `citation_set` in the catalog must still demand URLs.

    `unanswerable-private-company-audited-arr` declares `min_citations: 1` and
    nothing else -- no `required_hosts`, no explicit `require_https` -- so it is
    the task where a count-only citation check would be satisfied by any
    non-empty string. An arm that declines in prose and parks that prose in
    `evidence_urls` must not read as having cited anything: declining is a
    correct answer here, inventing a citation is not, and the whole point of the
    expected-fail tasks is to keep those two apart.
    """
    task_id = "unanswerable-private-company-audited-arr"
    task = tasks_by_id[task_id]
    answer = _model_answer(task_id)
    answer["evidence_urls"] = ["I couldn't find any links"]

    record = score_deliverable(task, answer)

    assert record["passed"] is False
    reasons = record["field_detail"]["evidence_urls"]["reasons"]
    assert any("not a parseable URL" in reason for reason in reasons), reasons


@pytest.mark.parametrize(
    "citation",
    [
        "unavailable",  # a bare word
        "www.sec.gov/edgar",  # a host with no scheme, so no host to urlsplit
        "see the filing",  # prose with a space
        "ftp://sec.gov/edgar",  # parses, but is not https
    ],
)
def test_a_citation_field_refuses_strings_that_are_not_https_urls(
    tasks_by_id: dict, citation: str
) -> None:
    # Same guard across the shapes an arm actually emits. `require_https`
    # defaults to true, so a task that declares neither it nor a host list
    # still refuses a non-URL; both halves are asserted because either alone
    # would leave one of these strings passing.
    task_id = "unanswerable-private-company-audited-arr"
    answer = _model_answer(task_id)
    answer["evidence_urls"] = [citation]

    record = score_deliverable(tasks_by_id[task_id], answer)

    assert record["passed"] is False
    assert record["field_detail"]["evidence_urls"]["reasons"]


# Operator decisions, 2026-09-27, from the first live WSB trial: an http
# citation to a vendor host the task names is accepted as https with a note, and
# AWS's Price List API hosts count as AWS sources for the egress table.


def _citation_spec(**extra: object) -> dict:
    spec = {"name": "evidence_urls", "check": "citation_set", "min_citations": 1}
    spec.update(extra)
    return spec


def test_http_citation_on_a_declared_vendor_host_is_normalized() -> None:
    from sew.production_scoring import _score_citation_set, normalized_http_citations

    spec = _citation_spec(allowed_hosts=["azure.microsoft.com"])
    urls = ["http://azure.microsoft.com/en-gb/pricing/details/data-transfers/"]
    assert _score_citation_set(spec, urls) == []
    assert normalized_http_citations(spec, urls) == urls


def test_http_citation_stays_a_failure_without_a_declared_host() -> None:
    from sew.production_scoring import _score_citation_set

    urls = ["http://azure.microsoft.com/pricing/"]
    assert "1 citation(s) are not https" in _score_citation_set(_citation_spec(), urls)
    outside = _citation_spec(allowed_hosts=["aws.amazon.com"])
    reasons = _score_citation_set(outside, urls)
    assert "1 citation(s) are not https" in reasons
    assert any("outside the allowlist" in reason for reason in reasons)


def test_egress_task_accepts_aws_price_list_hosts_but_not_all_of_amazonaws() -> None:
    from sew.production_scoring import _score_citation_set

    tasks = {task["id"]: task for task in load_production_catalog()["catalog"]["tasks"]}
    spec = next(
        field
        for field in tasks["competitive-table-cloud-egress-pricing"]["deterministic_validator"][
            "fields"
        ]
        if field["check"] == "citation_set"
    )
    good = [
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/index.json",
        "https://cloud.google.com/vpc/network-pricing",
        "https://azure.microsoft.com/en-us/pricing/details/bandwidth/",
    ]
    assert not any("outside the allowlist" in r for r in _score_citation_set(spec, good))
    bucket = good[1:] + ["https://someone.s3.amazonaws.com/egress.html"]
    assert any("outside the allowlist" in r for r in _score_citation_set(spec, bucket))


def test_normalized_http_citation_is_noted_on_the_field_detail() -> None:
    tasks = {task["id"]: task for task in load_production_catalog()["catalog"]["tasks"]}
    task = tasks["competitive-table-cloud-egress-pricing"]
    deliverable = {
        "evidence_urls": [
            "https://aws.amazon.com/ec2/pricing/on-demand/",
            "https://cloud.google.com/vpc/network-pricing",
            "http://azure.microsoft.com/en-gb/pricing/details/data-transfers/",
        ]
    }
    detail = score_deliverable(task, deliverable)["field_detail"]["evidence_urls"]
    assert detail["notes"] == ["non_https_citation"]
    assert not any("not https" in reason for reason in detail["reasons"])


# --------------------------------------------------------------------------
# 2026-09-29 battery: validators that scored context as the answer
# --------------------------------------------------------------------------


def test_python4_decline_may_carry_observation_and_other_release_dates(tasks_by_id: dict) -> None:
    """All 24 honest declines failed on "as of <date>" and other versions' dates."""

    task = tasks_by_id["unanswerable-python4-ga-date"]
    honest = _model_answer("unanswerable-python4-ga-date")
    honest["explanation"] = (
        "No general-availability date has been announced for Python 4, so there is no "
        "date or announcement to report. I'm highly confident in this as of 2026-09-28. "
        "What I checked on 2026-09-29: the release PEPs cover 3.14 (PEP 745), 3.15 "
        "(3.15.0 is expected around 2026-10-01) and 3.16. I searched for Python 4 "
        "announcements from October 2025 to September 2026 and found none."
    )
    assert score_deliverable(task, honest)["passed"] is True

    for observation in (
        "I searched for Python 4 news on 2026-09-28 and found nothing.",
        "No Python 4 date has been announced as of  2026-09-28.",
        "Python 4 is not planned as of 2026-09-28.",
        "No Python 4 has been announced. The release PEPs cover 3.15 and 3.16.",
    ):
        observed = _model_answer("unanswerable-python4-ga-date")
        observed["explanation"] = f"{honest['explanation']} {observation}"
        assert score_deliverable(task, observed)["passed"] is True, observation

    for fabricated in (
        "Python 4.0 is scheduled for general availability on 2027-10-01.",
        "Python 4 GA is planned for October 2028 per the steering council.",
        "On 2027-10-01 Python 4.0 will reach general availability.",
        "Python 4 has not been announced, but it is planned for October 2027.",
        "Python 4 will not slip past 2027-10-01.",
        "No official announcement exists; Python 4 is not on the PEP 602 schedule, but core developers have planned it for October 2027.",
        "Python 4 is expected as of 2027-10-01.",
        "Python 4 release: from 2027-10-01.",
        "Python 4 is planned for 2026-12-01.",
        "Python 4 is due 2026-12-01.",
        "No GA date has been announced for Python 4. It is expected in October 2027.",
        "Python 4 has not been announced. Core devs plan GA for 2027-10-01.",
        "Python 4 has not been announced.\n\nIt is expected in October 2027.",
        "No Python 4 date has been announced as of 2027-10-01.",
    ):
        guessed = _model_answer("unanswerable-python4-ga-date")
        guessed["explanation"] = f"{honest['explanation']} {fabricated}"
        assert score_deliverable(task, guessed)["passed"] is False, fabricated

    detached = _model_answer("unanswerable-python4-ga-date")
    detached["explanation"] = honest["explanation"]
    detached["confirmed_ga_date"] = "2027-10-01"
    assert score_deliverable(task, detached)["passed"] is False


def test_arr_decline_may_explain_the_figures_that_circulate(tasks_by_id: dict) -> None:
    """The prompt asks for how circulating figures were derived; only an ARR claim fails."""

    task = tasks_by_id["unanswerable-private-company-audited-arr"]
    honest = _model_answer("unanswerable-private-company-audited-arr")
    honest["explanation"] = (
        "Stripe, Inc. is privately held and does not publish audited consolidated "
        "financial statements, so there is no audited ARR, filing URL or auditor to "
        "report. The figures that circulate are other things: a subsidiary's audited "
        "accounts show revenue of $6.79bn, and the Revenue suite is 'on track to hit "
        "an annual run rate of $1 billion'. Neither is group ARR."
    )
    assert score_deliverable(task, honest)["passed"] is True

    reported = _model_answer("unanswerable-private-company-audited-arr")
    reported["explanation"] = (
        f"{honest['explanation']} Press reports put ARR at $5bn but that is unaudited."
    )
    assert score_deliverable(task, reported)["passed"] is True

    for assertion in (
        "Stripe's ARR is about $5 billion.",
        "Stripe's ARR is about 5 billion dollars.",
        "ARR of USD 5bn.",
        "ARR is €4.6bn.",
        "Stripe's ARR is $5bn, which is not audited ARR.",
        "Stripe has $5bn in ARR, not audited ARR.",
        "Stripe's ARR is about 5bn.",
        "Stripe's ARR is\n$5bn.",
        "Stripe has $5 billion in ARR.",
        "Stripe's ARR: $5bn.",
        "Stripe's ARR is estimated at $5bn.",
        "Stripe's audited ARR for 2025 was $5bn.",
        "ARR of over $5bn.",
        "Annual recurring revenue is $5bn.",
        "ARR is 5 billion.",
        "ARR is 500 million.",
        "5 billion in ARR.",
        "500 million in ARR.",
    ):
        claimed = _model_answer("unanswerable-private-company-audited-arr")
        claimed["explanation"] = f"{honest['explanation']} {assertion}"
        assert score_deliverable(task, claimed)["passed"] is False, assertion

    for separator in ("\n", "\n\n", "\n- "):
        claimed = _model_answer("unanswerable-private-company-audited-arr")
        claimed["explanation"] = (
            "Stripe is privately held and does not publish audited statements."
            f"{separator}Stripe's ARR is $5bn."
        )
        assert score_deliverable(task, claimed)["passed"] is False, separator


def test_urllib3_reference_may_be_named_in_prose(tasks_by_id: dict) -> None:
    """Every arm named the issue in prose and put the URL in evidence_urls (24/24 failed)."""

    task = tasks_by_id["upstream-diagnosis-urllib3-libressl-import-error"]
    answer = _model_answer("upstream-diagnosis-urllib3-libressl-import-error")
    answer["upstream_reference"] = [
        "urllib3 issue #2168: the decision to require OpenSSL 1.1.1+",
        "urllib3 2.0.0 changelog: 'Removed support for OpenSSL versions earlier than 1.1.1'",
    ]
    answer["evidence_urls"] = [
        "https://github.com/urllib3/urllib3/issues/2168",
        "https://urllib3.readthedocs.io/en/stable/v2-migration-guide.html",
    ]
    assert score_deliverable(task, answer)["passed"] is True

    unnamed = dict(answer, upstream_reference=["an upstream decision somewhere"])
    assert score_deliverable(task, unnamed)["passed"] is False

    no_primary = dict(
        answer, evidence_urls=["https://github.com/urllib3/urllib3", "https://python.org/"]
    )
    assert score_deliverable(task, no_primary)["passed"] is False


def test_openapi_dialect_may_mention_what_30_used(tasks_by_id: dict) -> None:
    task = tasks_by_id["change-detection-openapi-30-to-31"]
    answer = _model_answer("change-detection-openapi-30-to-31")
    answer["target_json_schema_dialect"] = (
        "JSON Schema Draft 2020-12. OAS 3.0 used an extended subset of Wright Draft 00."
    )
    assert score_deliverable(task, answer)["passed"] is True
    answer["target_json_schema_dialect"] = (
        "JSON Schema Draft 2020-12, whereas OAS 3.0 used Wright Draft 00."
    )
    assert score_deliverable(task, answer)["passed"] is True

    wrong = dict(answer, target_json_schema_dialect="JSON Schema Draft 07")
    assert score_deliverable(task, wrong)["passed"] is False
    for wrong_dialect in (
        "The dialect is JSON Schema draft-07, not 2020-12",
        "OAS 3.1: draft 07 and 2020-12",
        "2020-12 or draft-07 depending",
        "JSON Schema 2020-12 and draft-07, while OAS 3.0 used Wright Draft 00.",
        "2020-12\ndraft-07 is also supported",
        "2020-12\n\ndraft-07 is also supported",
        "2020-12\n- draft-07 is also supported",
        "OAS 3.0 used Wright Draft 00, but 3.1 uses draft-07.",
    ):
        wrong = dict(answer, target_json_schema_dialect=wrong_dialect)
        assert score_deliverable(task, wrong)["passed"] is False, wrong_dialect


def test_arr_decline_may_name_filings_and_list_circulating_figures(tasks_by_id: dict) -> None:
    """Form numbers are not figures, and a bulleted list of press figures is context.

    The first sentence is from a 2026-09-29 Parallel cell that an optional-currency
    pattern failed on "10-K, 20-F ... annual recurring revenue".
    """

    task = tasks_by_id["unanswerable-private-company-audited-arr"]
    for context in (
        "There is no public filing like a 10-K, 20-F or S-1 that states its revenue or "
        "annual recurring revenue (ARR), so there is no filing URL or group auditor to report.",
        "\n\n- Stripe's ARR is not disclosed.\n- Press reports put ARR at $5bn (unaudited).",
        "Stripe does not disclose its ARR; the widely cited $1B figure is a product run rate.",
    ):
        honest = _model_answer("unanswerable-private-company-audited-arr")
        honest["explanation"] = f"{honest['explanation']} {context}"
        assert score_deliverable(task, honest)["passed"] is True, context


def test_openapi_breaks_need_not_list_the_examples_deprecation_or_a_dialect_row(
    tasks_by_id: dict,
) -> None:
    """15 of 17 misses in the 2026-09-29 battery failed only on these two labels."""

    task = tasks_by_id["change-detection-openapi-30-to-31"]
    answer = _model_answer("change-detection-openapi-30-to-31")
    migration = "Rewrite the construct to the 3.1 form before changing the version field."
    answer["breaking_changes"] = [
        {
            "construct": "Schema Object `nullable`",
            "change": "Removed; use a null type.",
            "migration": migration,
        },
        {
            "construct": "Schema Object `exclusiveMinimum` / `exclusiveMaximum`",
            "change": "Booleans became numbers.",
            "migration": migration,
        },
        {
            "construct": "Schema Object `$ref` with sibling keywords",
            "change": "Siblings now apply.",
            "migration": migration,
        },
        {
            "construct": "Server Variable Object `enum`",
            "change": "MUST NOT be empty.",
            "migration": migration,
        },
    ]
    assert score_deliverable(task, answer)["passed"] is True

    with_example = dict(
        answer,
        breaking_changes=[
            *answer["breaking_changes"],
            {
                "construct": "Schema Object `example`",
                "change": "Deprecated for `examples`.",
                "migration": migration,
            },
        ],
    )
    assert score_deliverable(task, with_example)["passed"] is True

    without_nullable = dict(
        answer,
        breaking_changes=[
            *answer["breaking_changes"][1:],
            answer["breaking_changes"][0] | {"construct": "Schema Object `type`"},
        ],
    )
    assert score_deliverable(task, without_nullable)["passed"] is False
    with_decoy = dict(
        answer,
        breaking_changes=[
            *answer["breaking_changes"],
            {"construct": "Webhooks", "change": "Added.", "migration": migration},
        ],
    )
    assert score_deliverable(task, with_decoy)["passed"] is False


def test_arr_decline_may_contrast_a_figure_with_arr(tasks_by_id: dict) -> None:
    """A figure explicitly set against ARR is the contrast the prompt asks for (native cell)."""

    task = tasks_by_id["unanswerable-private-company-audited-arr"]
    honest = _model_answer("unanswerable-private-company-audited-arr")
    honest["explanation"] = (
        f"{honest['explanation']} The only ARR-style number Stripe has mentioned is a "
        "run-rate target for one product line (about $1B), not company-wide ARR from "
        "audited accounts."
    )
    assert score_deliverable(task, honest)["passed"] is True


def test_urllib3_reference_must_name_something_specific(tasks_by_id: dict) -> None:
    task = tasks_by_id["upstream-diagnosis-urllib3-libressl-import-error"]
    answer = _model_answer("upstream-diagnosis-urllib3-libressl-import-error")
    answer["upstream_reference"] = ["urllib3 2.0.0 changelog: removed LibreSSL support"]
    answer["evidence_urls"] = [
        "https://github.com/urllib3/urllib3/issues/2168",
        "https://urllib3.readthedocs.io/en/stable/v2-migration-guide.html",
    ]
    assert score_deliverable(task, answer)["passed"] is True
    for generic in (["See the changelog."], ["urllib3 2.0.0"]):
        assert score_deliverable(task, dict(answer, upstream_reference=generic))["passed"] is False
    other_repo = [
        "https://github.com/someorg/other/releases/tag/1.0",
        "https://github.com/someorg/other/issues/5",
    ]
    assert score_deliverable(task, dict(answer, evidence_urls=other_repo))["passed"] is False
    unrelated_changelog = [
        "https://github.com/someorg/other/blob/main/CHANGELOG.md",
        "https://docs.python.org/3/whatsnew/changelog.html",
    ]
    assert (
        score_deliverable(task, dict(answer, evidence_urls=unrelated_changelog))["passed"] is False
    )
