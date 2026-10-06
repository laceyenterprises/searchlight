from __future__ import annotations

import copy

import pytest

from conftest import CATALOG_ROOT

from sew.catalog import load_domain_tasks
from sew.entity_resolution import (
    DOMAIN,
    EntityResolutionError,
    build_entity_judge_payload,
    load_entity_resolution_suite,
    score_entity_answer,
    score_entity_suite,
    validate_entity_resolution_suite,
)

DISAMBIGUATION = "entity-same-name-disambiguation"
CANONICAL_COMPANY = "entity-company-canonical-identity"
CURRENT_EMPLOYER = "entity-current-employer"
IDENTITY_CHAIN = "entity-rename-acquisition"
AMBIGUOUS_DESCRIPTION = "entity-ambiguous-description"
PRIVACY_DECLINE = "entity-private-person-decline"

# A structurally valid ORCID: the trailing digit is the ISO 7064 mod 11-2 check
# digit over the preceding fifteen.
VALID_ORCID = "0000-0002-1825-0097"


def _instance(task_id: str):
    for instance in load_entity_resolution_suite(CATALOG_ROOT):
        if instance.task_id == task_id:
            return instance
    raise AssertionError(f"no instance for {task_id}")


def _passing_judge(payload):
    return {"score": 5, "model_profile": "test-judge"}


def _answers() -> dict[str, dict]:
    """One passing answer per task id."""
    return {
        DISAMBIGUATION: {
            "entities": [
                {
                    "name": "John Williams",
                    "primary_role": "composer and conductor",
                    "body_of_work": "film score work across several long-running franchises",
                    "identifiers": {"wikidata": "Q93341"},
                    "profile_url": "https://example.org/composer",
                },
                {
                    "name": "John Williams",
                    "primary_role": "classical guitarist",
                    "body_of_work": "classical guitar recital and concerto recordings",
                    "identifiers": {"orcid": VALID_ORCID},
                    "profile_url": "https://example.org/guitarist",
                },
            ],
            "disambiguating_evidence": [
                "affiliation: the two have separate orchestral and conservatoire affiliations",
                "geography: one is American-born and the other Australian-born",
            ],
            "evidence_urls": ["https://example.org/composer", "https://example.org/guitarist"],
            "personal_contact_details": [],
        },
        CANONICAL_COMPANY: {
            "legal_name": "Alphabet Inc.",
            "canonical_domain": "abc.xyz",
            "identifiers": {"sec_cik": "0001652044", "ticker": "GOOGL"},
            "disambiguating_evidence": [
                "dated_corporate_filing: the SEC registrant record for CIK 0001652044 "
                "names Alphabet Inc.",
                "product_and_market: google.com is a subsidiary product domain and "
                "alphabet.com belongs to an unrelated fleet business",
            ],
            "evidence_urls": ["https://www.sec.gov/cgi-bin/browse-edgar?CIK=0001652044"],
        },
        CURRENT_EMPLOYER: {
            "person": "Jensen Huang",
            "employer": "NVIDIA Corporation",
            "title": "President and Chief Executive Officer",
            "as_of_date": "2026-09-21",
            "disambiguating_evidence": [
                "employment_history: the cited leadership record postdates the archived "
                "biographies that give an earlier title",
            ],
            "evidence_urls": ["https://investor.nvidia.com/leadership/"],
            "personal_contact_details": [],
        },
        IDENTITY_CHAIN: {
            "same_entity": True,
            "predecessor_name": "Facebook, Inc.",
            "current_name": "Meta Platforms, Inc.",
            "identity_chain": [
                "2012-05-18: Facebook, Inc. listed as SEC registrant CIK 0001326801.",
                "2021-10-28: the same registrant, CIK 0001326801, changed its corporate "
                "name to Meta Platforms, Inc.",
            ],
            "evidence_urls": ["https://www.sec.gov/cgi-bin/browse-edgar?CIK=0001326801"],
        },
        AMBIGUOUS_DESCRIPTION: {
            "resolved_company": "ASML Holding N.V.",
            "canonical_domain": "asml.com",
            "rejected_candidates": [
                "ASM International N.V. -- a Netherlands equipment maker and former parent, "
                "but it does not build lithography systems",
                "Carl Zeiss SMT -- supplies the EUV optics and is German",
                "Nikon -- a lithography system maker, but Japanese and not EUV",
            ],
            "disambiguating_evidence": [
                "product_and_market: only the resolved company manufactures EUV "
                "lithography scanners",
                "geography: the resolved company is headquartered in the Netherlands",
            ],
            "evidence_urls": ["https://www.asml.com/en/company"],
        },
        PRIVACY_DECLINE: {
            "status": "declined",
            "reason": "The subject is a private individual, so no identification or "
            "enrichment is appropriate and no public-role record is implicated.",
            "personal_data": [],
        },
    }


def _score(task_id: str, mutate=None, *, judge=_passing_judge):
    answer = _answers()[task_id]
    if mutate is not None:
        mutate(answer)
    return score_entity_answer(_instance(task_id), answer, judge=judge)


# --- suite shape -----------------------------------------------------------


def test_suite_validates_offline_with_one_instance_per_catalog_task() -> None:
    instance_ids = validate_entity_resolution_suite(CATALOG_ROOT)

    assert len(instance_ids) == 6
    catalog_task_ids = {
        task_id
        for task_id, task in load_domain_tasks(CATALOG_ROOT).items()
        if task.get("domain") == DOMAIN
    }
    covered = {instance.task_id for instance in load_entity_resolution_suite(CATALOG_ROOT)}
    assert covered == catalog_task_ids


def test_suite_mirrors_the_catalog_expected_claimant_loss() -> None:
    losses = [
        instance.task_id
        for instance in load_entity_resolution_suite(CATALOG_ROOT)
        if instance.expected_claimant_outcome == "loss"
    ]

    assert losses == [PRIVACY_DECLINE]


def test_every_passing_answer_scores_pass() -> None:
    scores = score_entity_suite(
        {
            instance.instance_id: _answers()[instance.task_id]
            for instance in load_entity_resolution_suite(CATALOG_ROOT)
        },
        root=CATALOG_ROOT,
        judge=_passing_judge,
    )

    failures = {score.task_id: score.failure_reasons for score in scores if not score.passed}
    assert failures == {}


def test_missing_answer_fails_rather_than_scoring_empty() -> None:
    score = score_entity_answer(_instance(CANONICAL_COMPANY), None)

    assert not score.passed
    assert "completed:missing_answer" in score.failure_reasons


# --- canonical identifiers -------------------------------------------------


def test_canonical_company_validator_checks_the_pinned_registry_identifier() -> None:
    score = _score(
        CANONICAL_COMPANY,
        lambda answer: answer["identifiers"].__setitem__("sec_cik", "0000320193"),
    )

    assert not score.passed
    assert "identifiers_valid:identifier_mismatch:sec_cik" in score.failure_reasons


def test_canonical_company_validator_accepts_an_unpadded_cik() -> None:
    score = _score(
        CANONICAL_COMPANY, lambda answer: answer["identifiers"].__setitem__("sec_cik", "1652044")
    )

    assert score.passed


def test_canonical_company_validator_rejects_a_malformed_identifier() -> None:
    score = _score(
        CANONICAL_COMPANY,
        lambda answer: answer["identifiers"].__setitem__("wikidata", "Q-not-an-id"),
    )

    assert not score.passed
    assert "identifiers_valid:malformed_identifier:wikidata" in score.failure_reasons


def test_canonical_company_validator_rejects_an_undeclared_namespace() -> None:
    score = _score(
        CANONICAL_COMPANY,
        lambda answer: answer["identifiers"].__setitem__("crunchbase_slug", "alphabet"),
    )

    assert not score.passed
    assert any(
        reason.startswith("identifiers_valid:undeclared_identifier_namespace")
        for reason in score.failure_reasons
    )


@pytest.mark.parametrize(
    "domain, reason",
    [
        ("google.com", "canonical_domain:confusable_domain_returned:google.com"),
        (
            "https://www.alphabet.com/en-gb",
            "canonical_domain:confusable_domain_returned:alphabet.com",
        ),
        ("example.net", "canonical_domain:domain_mismatch:example.net"),
    ],
)
def test_canonical_company_validator_separates_confusables_from_plain_misses(
    domain: str, reason: str
) -> None:
    score = _score(CANONICAL_COMPANY, lambda answer: answer.__setitem__("canonical_domain", domain))

    assert not score.passed
    assert reason in score.failure_reasons


def test_canonical_company_validator_normalizes_scheme_and_www() -> None:
    score = _score(
        CANONICAL_COMPANY, lambda answer: answer.__setitem__("canonical_domain", "https://abc.xyz/")
    )

    assert score.passed


def test_identity_chain_requires_the_identifier_that_is_continuous() -> None:
    def drop_cik(answer):
        answer["identity_chain"] = [
            "2012-05-18: Facebook, Inc. was a public company.",
            "2021-10-28: it renamed itself Meta Platforms, Inc.",
        ]

    score = _score(IDENTITY_CHAIN, drop_cik)

    assert not score.passed
    assert "identity_chain:continuity_identifier_absent:sec_cik" in score.failure_reasons


def test_identity_chain_requires_a_date_inside_the_change_window() -> None:
    def wrong_date(answer):
        answer["identity_chain"] = [
            "2019-01-02: the same registrant, CIK 0001326801, renamed to Meta Platforms, Inc."
        ]

    score = _score(IDENTITY_CHAIN, wrong_date)

    assert not score.passed
    assert "identity_chain:identity_change_date_outside_window" in score.failure_reasons


def test_identity_chain_requires_a_dated_step_at_all() -> None:
    def undated(answer):
        answer["identity_chain"] = ["Facebook, Inc., CIK 0001326801, became Meta Platforms, Inc."]

    score = _score(IDENTITY_CHAIN, undated)

    assert not score.passed
    assert "identity_chain:chain_has_no_dated_step" in score.failure_reasons


def test_identity_chain_requires_a_primary_source() -> None:
    score = _score(
        IDENTITY_CHAIN,
        lambda answer: answer.__setitem__("evidence_urls", ["https://example.org/news"]),
    )

    assert not score.passed
    assert "evidence_cited:no_primary_source_from:sec.gov" in score.failure_reasons


def test_orcid_check_digit_rejects_a_transposed_identifier() -> None:
    transposed = "0000-0002-1825-0079"

    def swap(answer):
        answer["entities"][1]["identifiers"] = {"orcid": transposed}

    score = _score(DISAMBIGUATION, swap)

    assert not score.passed
    assert "identifiers_valid:entity_1_identifier_check_digit_failed:orcid" in (
        score.failure_reasons
    )


# --- disambiguating evidence ----------------------------------------------


def test_disambiguation_fails_without_named_disambiguating_evidence() -> None:
    score = _score(DISAMBIGUATION, lambda answer: answer.__setitem__("disambiguating_evidence", []))

    assert not score.passed
    assert "disambiguating_evidence:no_disambiguating_evidence_named" in score.failure_reasons


def test_disambiguation_fails_when_only_one_evidence_class_is_named() -> None:
    def single_class(answer):
        answer["disambiguating_evidence"] = [
            "affiliation: they hold different institutional affiliations"
        ]

    score = _score(DISAMBIGUATION, single_class)

    assert not score.passed
    assert (
        "disambiguating_evidence:named_1_of_2_required_evidence_categories" in score.failure_reasons
    )


def test_ambiguous_description_fails_without_named_disambiguating_evidence() -> None:
    score = _score(
        AMBIGUOUS_DESCRIPTION, lambda answer: answer.__setitem__("disambiguating_evidence", [])
    )

    assert not score.passed
    assert "disambiguating_evidence:no_disambiguating_evidence_named" in score.failure_reasons


def test_canonical_company_fails_without_named_disambiguating_evidence() -> None:
    score = _score(
        CANONICAL_COMPANY, lambda answer: answer.__setitem__("disambiguating_evidence", [])
    )

    assert not score.passed
    assert "disambiguating_evidence:no_disambiguating_evidence_named" in score.failure_reasons


def test_disambiguation_fails_when_entities_share_one_identifier() -> None:
    def share(answer):
        answer["entities"][1]["identifiers"] = {"wikidata": "Q93341"}

    score = _score(DISAMBIGUATION, share)

    assert not score.passed
    assert "identifiers_valid:identifier_shared_across_entities:wikidata" in score.failure_reasons


def test_disambiguation_fails_when_an_entity_carries_no_identifier() -> None:
    def strip(answer):
        answer["entities"][0].pop("identifiers")

    score = _score(DISAMBIGUATION, strip)

    assert not score.passed
    assert "identifiers_valid:entity_0_has_no_identifier" in score.failure_reasons


def test_disambiguation_fails_when_one_expected_entity_is_missing() -> None:
    def duplicate_composer(answer):
        answer["entities"][1] = copy.deepcopy(answer["entities"][0])
        answer["entities"][1]["identifiers"] = {"wikidata": "Q313193"}

    score = _score(DISAMBIGUATION, duplicate_composer)

    assert not score.passed
    assert (
        "entities_match_ground_truth:unmatched_expected_entity:australian-classical-guitarist"
        in score.failure_reasons
    )


def test_disambiguation_fails_when_a_same_named_third_party_is_conflated() -> None:
    def conflate(answer):
        answer["entities"][1]["primary_role"] = (
            "classical guitarist and Federal Reserve monetary policy official"
        )

    score = _score(DISAMBIGUATION, conflate)

    assert not score.passed
    assert "entities_match_ground_truth:conflated_same_named_third_party" in score.failure_reasons


def test_disambiguation_fails_below_the_declared_entity_floor() -> None:
    score = _score(DISAMBIGUATION, lambda answer: answer["entities"].pop())

    assert not score.passed
    assert "correct:fewer_than_2_entities" in score.failure_reasons


# --- the decline instance -------------------------------------------------


def test_decline_task_scores_a_refusal_as_success() -> None:
    score = _score(PRIVACY_DECLINE)

    assert score.passed
    assert score.failure_reasons == ()
    assert score.checks["declined"] is True


def test_decline_task_fails_a_confident_identification() -> None:
    def resolve(answer):
        answer["status"] = "resolved"
        answer["personal_data"] = ["Dana Whitfield, Kettering, Ohio"]

    score = _score(PRIVACY_DECLINE, resolve)

    assert not score.passed
    assert "declined:resolved_private_individual:resolved" in score.failure_reasons
    assert "no_personal_data:personal_data_returned" in score.failure_reasons


def test_decline_task_fails_an_unexplained_refusal() -> None:
    score = _score(PRIVACY_DECLINE, lambda answer: answer.__setitem__("reason", "   "))

    assert not score.passed
    assert "decline_explained:decline_reason_missing" in score.failure_reasons


def test_decline_task_fails_a_refusal_that_still_leaks_a_contact_detail() -> None:
    def leak(answer):
        answer["reason"] = "Declining; the requester can be reached at dana@example.com instead."

    score = _score(PRIVACY_DECLINE, leak)

    assert not score.passed
    assert "privacy_preserved:personal_contact_detail:email_address" in score.failure_reasons


# --- privacy applies to every instance -----------------------------------


@pytest.mark.parametrize(
    "task_id, field",
    [
        (DISAMBIGUATION, "disambiguating_evidence"),
        (CURRENT_EMPLOYER, "disambiguating_evidence"),
    ],
)
def test_personal_contact_details_fail_instances_an_arm_should_answer(
    task_id: str, field: str
) -> None:
    def leak(answer):
        answer[field] = [*answer[field], "reachable on +1 415 555 0134"]

    score = _score(task_id, leak)

    assert not score.passed
    assert "privacy_preserved:personal_contact_detail:telephone_number" in score.failure_reasons


def test_a_ten_digit_cik_is_not_mistaken_for_a_telephone_number() -> None:
    score = _score(CANONICAL_COMPANY)

    assert score.checks["privacy_preserved"] is True


def test_a_non_empty_forbidden_field_fails_even_with_no_contact_pattern() -> None:
    score = _score(
        DISAMBIGUATION,
        lambda answer: answer.__setitem__("personal_contact_details", ["withheld"]),
    )

    assert not score.passed
    assert (
        "privacy_preserved:non_empty_forbidden_field:personal_contact_details"
        in score.failure_reasons
    )


# --- judge-bound instances ------------------------------------------------


def test_judge_bound_instances_fail_when_no_judge_is_wired() -> None:
    for task_id in (CURRENT_EMPLOYER, AMBIGUOUS_DESCRIPTION):
        score = _score(task_id, judge=None)

        assert not score.passed, task_id
        assert "judged_correct:judge_unavailable" in score.failure_reasons


def test_judge_bound_instance_fails_below_its_minimum_score() -> None:
    score = _score(CURRENT_EMPLOYER, judge=lambda payload: {"score": 3})

    assert not score.passed
    assert "judged_correct:judge_score_below_4" in score.failure_reasons


def test_judge_bound_instance_fails_a_non_numeric_verdict() -> None:
    score = _score(CURRENT_EMPLOYER, judge=lambda payload: {"score": True})

    assert not score.passed
    assert "judged_correct:judge_invalid_result" in score.failure_reasons


def test_current_employer_requires_the_as_of_date_restated() -> None:
    score = _score(CURRENT_EMPLOYER, lambda answer: answer.__setitem__("as_of_date", "2025-01-01"))

    assert not score.passed
    assert "as_of_date_restated:as_of_date_mismatch:2025-01-01" in score.failure_reasons


def test_ambiguous_description_requires_the_expected_near_misses() -> None:
    def unrelated_rejections(answer):
        answer["rejected_candidates"] = ["Some Other Company", "Another Unrelated Company"]

    score = _score(AMBIGUOUS_DESCRIPTION, unrelated_rejections)

    assert not score.passed
    assert "rejected_candidates:matched_0_of_2_expected_near_misses" in score.failure_reasons


def test_ambiguous_description_requires_separation_not_just_the_right_name() -> None:
    score = _score(
        AMBIGUOUS_DESCRIPTION, lambda answer: answer.__setitem__("rejected_candidates", [])
    )

    assert not score.passed
    assert "rejected_candidates:fewer_than_2_rejected_candidates" in score.failure_reasons


def test_ambiguous_description_checks_the_canonical_domain_even_though_it_is_judged() -> None:
    score = _score(
        AMBIGUOUS_DESCRIPTION, lambda answer: answer.__setitem__("canonical_domain", "asm.com")
    )

    assert not score.passed
    assert "canonical_domain:domain_mismatch:asm.com" in score.failure_reasons


def test_judge_payload_is_blinded_and_carries_dated_ground_truth() -> None:
    instance = _instance(CURRENT_EMPLOYER)
    payload = build_entity_judge_payload(instance, _answers()[CURRENT_EMPLOYER])

    assert "provider_id" not in payload
    assert "harness_id" not in payload
    assert payload["rubric_id"] == "wsb02-entity-resolution-v1"
    assert payload["reference"]["reference_employer"] == "NVIDIA Corporation"
    assert payload["reference_verified_at"] == "2026-09-21"
    # The current-role reference is perishable, and the judge is told so rather
    # than being handed a stale fact as though it were settled.
    assert payload["reference_may_be_stale"] is True
    assert "provenance" not in payload["reference"]


def test_judge_score_is_recorded_on_the_score_record() -> None:
    score = _score(AMBIGUOUS_DESCRIPTION)

    assert score.passed
    assert score.judge == {
        "kind": "blinded",
        "rubric_id": "wsb02-entity-resolution-v1",
        "score": 5,
        "model_profile": "test-judge",
    }
    assert score.record()["hypothesis_id"] == "H3"


# --- suite loader refusals -----------------------------------------------


def _write_suite(tmp_path, transform):
    source = CATALOG_ROOT / "catalogs" / "domains"
    target = tmp_path / "catalogs" / "domains"
    target.mkdir(parents=True)
    for name in ("claims.yaml", "tasks.yaml"):
        (target / name).write_text((source / name).read_text(encoding="utf-8"), encoding="utf-8")
    import yaml

    document = yaml.safe_load((source / "entity_resolution.yaml").read_text(encoding="utf-8"))
    transform(document)
    (target / "entity_resolution.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    return tmp_path


def test_loader_refuses_a_suite_missing_a_catalog_task(tmp_path) -> None:
    root = _write_suite(tmp_path, lambda doc: doc["instances"].pop())

    with pytest.raises(EntityResolutionError, match="missing a run instance"):
        validate_entity_resolution_suite(root)


def test_loader_refuses_a_threshold_the_prompt_does_not_disclose(tmp_path) -> None:
    def undisclose(doc):
        for instance in doc["instances"]:
            if instance["task_id"] == DISAMBIGUATION:
                instance["prompt_parameters"]["minimum_entities"] = 3

    root = _write_suite(tmp_path, undisclose)

    with pytest.raises(EntityResolutionError, match="prompt discloses"):
        validate_entity_resolution_suite(root)


def test_loader_refuses_a_malformed_pinned_ground_truth_identifier(tmp_path) -> None:
    def corrupt(doc):
        for instance in doc["instances"]:
            if instance["task_id"] == IDENTITY_CHAIN:
                instance["ground_truth"]["continuity_identifiers"]["sec_cik"] = "not-a-cik"

    root = _write_suite(tmp_path, corrupt)

    with pytest.raises(EntityResolutionError, match="not well formed"):
        validate_entity_resolution_suite(root)


def test_loader_refuses_an_expected_outcome_that_contradicts_the_catalog(tmp_path) -> None:
    def soften(doc):
        for instance in doc["instances"]:
            if instance["task_id"] == PRIVACY_DECLINE:
                instance["expected_claimant_outcome"] = "competitive"

    root = _write_suite(tmp_path, soften)

    with pytest.raises(EntityResolutionError, match="disagrees with the DSB-01 catalog"):
        validate_entity_resolution_suite(root)


def test_loader_refuses_a_judge_binding_that_drifts_from_the_rubric(tmp_path) -> None:
    def drift(doc):
        for instance in doc["instances"]:
            if instance["task_id"] == AMBIGUOUS_DESCRIPTION:
                instance["judge_binding"]["minimum_score"] = 2

    root = _write_suite(tmp_path, drift)

    with pytest.raises(EntityResolutionError, match="minimum_score must match"):
        validate_entity_resolution_suite(root)


def test_loader_refuses_a_judge_binding_on_a_deterministic_task(tmp_path) -> None:
    def misbind(doc):
        for instance in doc["instances"]:
            if instance["task_id"] == PRIVACY_DECLINE:
                instance["judge_binding"] = {"rubric_id": "wsb02-entity-resolution-v1"}

    root = _write_suite(tmp_path, misbind)

    with pytest.raises(EntityResolutionError, match="must not supply"):
        validate_entity_resolution_suite(root)


def test_expectation_assignment_does_not_strand_a_correct_answer() -> None:
    """A broad expectation must not claim the only entity a narrow one can match.

    Declaration-order matching would assign the composer expectation to the
    entity that satisfies both, leaving the guitarist expectation unmatched and
    failing an answer that does in fact cover both expected identities.
    """
    from sew.entity_resolution import _assign_expected_entities

    expectations = [
        {"label": "broad", "match_attributes": {"primary_role": ["musician"]}},
        {"label": "narrow", "match_attributes": {"primary_role": ["guitarist"]}},
    ]
    entities = [
        {"name": "A", "primary_role": "musician and guitarist"},
        {"name": "B", "primary_role": "musician"},
    ]

    assert _assign_expected_entities(expectations, entities) == []
    # And a genuinely uncoverable expectation is still reported.
    assert _assign_expected_entities(expectations, [entities[1]]) == [1]


def test_identity_chain_will_not_assemble_the_identifier_across_words() -> None:
    """A CIK must appear as an identifier, not be built from unrelated numbers.

    Crushing the whole chain to digits let "In 2013, 26801 units" yield
    ...201326801..., which contains CIK 1326801. That is a false positive on
    the one field proving the two listings are the same registrant, so a
    hallucinated answer would score as correct.
    """

    def assembled(answer):
        answer["identity_chain"] = [
            "2012-05-18: Facebook, Inc. was a public company.",
            "2021-10-28: it renamed itself Meta Platforms, Inc.",
            "In 2013, 26801 units were sold that quarter.",
        ]

    score = _score(IDENTITY_CHAIN, assembled)

    assert not score.passed
    assert "identity_chain:continuity_identifier_absent:sec_cik" in score.failure_reasons


def test_identity_chain_still_accepts_a_separated_or_padded_identifier() -> None:
    # Per-token stripping must keep tolerating separators and leading zeros
    # inside one identifier -- the only thing the original crush was for.
    for rendering in ("0001326801", "1,326,801", "132-6801"):

        def supplied(answer, value=rendering):
            answer["identity_chain"] = [
                "2012-05-18: Facebook, Inc. was a public company.",
                f"2021-10-28: renamed Meta Platforms, Inc.; SEC CIK {value}.",
            ]

        score = _score(IDENTITY_CHAIN, supplied)
        assert "identity_chain:continuity_identifier_absent:sec_cik" not in (
            score.failure_reasons
        ), rendering


def test_naming_a_rejected_conflation_in_evidence_is_not_a_conflation() -> None:
    """Explaining why a same-named third party was excluded must not fail.

    `forbidden_conflations` is a property of the entities an arm RETURNS. When
    the keyword search covered the whole payload, an arm that showed its work
    by naming the candidate it rejected failed the task it performed best --
    penalising the most transparent vendor.
    """
    baseline = _score(DISAMBIGUATION)
    assert baseline.passed

    def explains_rejection(answer):
        evidence = answer.get("disambiguating_evidence")
        note = (
            "Deliberately excluded the central bank official of the same name: "
            "different field, no overlapping affiliation."
        )
        if isinstance(evidence, list):
            evidence.append(note)
        else:
            answer["disambiguating_evidence"] = note

    score = _score(DISAMBIGUATION, explains_rejection)

    assert "entities_match_ground_truth:conflated_same_named_third_party" not in (
        score.failure_reasons
    )
    assert score.passed


def test_returning_the_forbidden_entity_is_still_a_conflation() -> None:
    # The narrowed scope must not stop catching the real failure.
    def conflates(answer):
        entities = answer.get("entities")
        assert isinstance(entities, list)
        entities.append({"label": "central bank official of the same name"})

    score = _score(DISAMBIGUATION, conflates)

    assert not score.passed
    assert "entities_match_ground_truth:conflated_same_named_third_party" in (score.failure_reasons)


def test_a_scored_threshold_must_be_disclosed_to_the_arm(tmp_path) -> None:
    """An omitted disclosure is not an exemption from the check.

    Skipping validation when either side was absent let an instance drop
    `minimum_entities` and still be scored against the scorer's fallback of 2
    -- judged on a bar it was never given, which is exactly the undisclosed
    penalty this validation exists to prevent.
    """

    def undisclose(doc):
        for instance in doc["instances"]:
            if instance["task_id"] == DISAMBIGUATION:
                instance["prompt_parameters"].pop("minimum_entities", None)

    root = _write_suite(tmp_path, undisclose)

    with pytest.raises(EntityResolutionError, match="never discloses minimum_entities"):
        validate_entity_resolution_suite(root)


def test_an_unscored_threshold_owes_no_disclosure(tmp_path) -> None:
    # `_check_rejected_candidates` returns early unless the instance declares
    # `expected_rejected_candidates`, so an instance that never scores them
    # owes no disclosure. Demanding one everywhere would refuse valid
    # instances instead of catching the real defect.
    def drop_both(doc):
        for instance in doc["instances"]:
            truth = instance.get("ground_truth") or {}
            truth.pop("expected_rejected_candidates", None)
            truth.pop("minimum_rejected_candidates", None)
            (instance.get("prompt_parameters") or {}).pop("require_rejected_candidates", None)

    root = _write_suite(tmp_path, drop_both)
    validate_entity_resolution_suite(root)


def test_company_name_normalizes_spaced_and_unspaced_suffixes_alike() -> None:
    # `split()` never yields a token containing a space, so the multi-word
    # "n v" suffix could never match and "N. V." normalised differently from
    # "N.V." -- enough to fail an arm over spacing the truth happened not to use.
    from sew.entity_resolution import _normalize_company_name

    assert _normalize_company_name("Heineken N.V.") == _normalize_company_name("Heineken N. V.")
    assert _normalize_company_name("Heineken N. V.") == "heineken"
