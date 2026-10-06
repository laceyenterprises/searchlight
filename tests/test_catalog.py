from __future__ import annotations

import pytest

from conftest import CATALOG_ROOT

from sew.schema import SchemaError
from sew.catalog import validate_domain_catalog, validate_lighthouse_catalog


def _copy_domain_catalog(tmp_path):
    source = CATALOG_ROOT / "catalogs" / "domains"
    target = tmp_path / "catalogs" / "domains"
    target.mkdir(parents=True)
    (target / "claims.yaml").write_text(
        (source / "claims.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target / "tasks.yaml").write_text(
        (source / "tasks.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return target


def test_lighthouse_catalog_has_initial_six_tasks() -> None:
    task_ids = validate_lighthouse_catalog(CATALOG_ROOT)

    assert task_ids == [
        "current-fact-lookup-v1",
        "freshness-stale-trap-v1",
        "docs-qa-fixture-v1",
        "no-reliable-answer-v1",
        "multi-source-synthesis-v1",
        "conflicting-source-synthesis-v1",
    ]


def test_lighthouse_catalog_rejects_traversal_task_ids(tmp_path) -> None:
    catalog_dir = tmp_path / "catalogs" / "lighthouse"
    catalog_dir.mkdir(parents=True)
    (catalog_dir / "suite.yaml").write_text(
        "\n".join(
            [
                "schema_version: 1",
                "suite_id: lighthouse",
                "version: '2026-09-16'",
                "description: test",
                "randomization_seed: 1",
                "fixture_mode: true",
                "repetitions: 1",
                "timeouts:",
                "  run_seconds: 1",
                "  provider_call_seconds: 1",
                "budgets:",
                "  max_provider_calls: 1",
                "  max_provider_result_chars: 1",
                "  max_total_tokens: 1",
                "providers: [native]",
                "harnesses:",
                "  codex:",
                "    model_profiles: [default]",
                "tasks: ['../outside']",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SchemaError, match="unsafe"):
        validate_lighthouse_catalog(tmp_path)


def test_domain_catalog_validates_all_tasks_offline() -> None:
    task_ids = validate_domain_catalog(CATALOG_ROOT)

    assert len(task_ids) == 24
    assert len(set(task_ids)) == 24


def test_domain_catalog_rejects_missing_expected_loss(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace("expected_claimant_outcome: loss", "expected_claimant_outcome: competitive")
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="expected-loss"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_blank_expected_loss_reason(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "expected_loss_reason: Precise symbol and call-graph lookup tests repository structure rather than GitHub page retrieval and should favor a code-aware index.",
            "expected_loss_reason: ''",
            1,
        )
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="expected loss requires a reason"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_task_without_validator_or_rubric(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "    deterministic_validator: {kind: github_reference, required_host: github.com, require_merged_pr: true, require_repository_match: true}\n",
            "",
            1,
        )
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="validator or rubric"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_unknown_hypothesis(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace("hypothesis_id: H1", "hypothesis_id: H9", 1)
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="unknown hypothesis_id"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_unknown_validator_kind(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace("kind: github_reference", "kind: github_referece", 1)
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="unknown deterministic_validator.kind"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_unknown_rubric_id(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace("rubric_id: wsb02-domain-evidence-v1", "rubric_id: wsb02-missing-v1", 1)
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="unknown judge_rubric.rubric_id"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_unknown_task_key(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "    prompt: Given a public repository",
            "    prompt_typo: Given a public repository",
            1,
        )
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="unknown key"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_missing_rubric_score_scale(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    tasks = (
        (target / "tasks.yaml")
        .read_text(encoding="utf-8")
        .replace(", score_scale: {min: 1, max: 5}", "", 1)
    )
    (target / "tasks.yaml").write_text(tasks, encoding="utf-8")

    with pytest.raises(SchemaError, match="score_scale"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_unknown_claim_strength(tmp_path) -> None:
    # Adversarial-review finding (PR #6923): the registry's `claim` text is a
    # capability description while `falsifiable_hypothesis` asserts head-to-head
    # superiority. `claim_strength` records which one the vendor's own source
    # actually makes, so a later "H1 REFUTED" does not attribute to a vendor a
    # performance claim its documentation never made.
    target = _copy_domain_catalog(tmp_path)
    claims = (
        (target / "claims.yaml")
        .read_text(encoding="utf-8")
        .replace(
            "claim_strength: vendor_ships_domain_surface",
            "claim_strength: vendor_is_probably_best",
            1,
        )
    )
    (target / "claims.yaml").write_text(claims, encoding="utf-8")

    with pytest.raises(SchemaError, match="claim_strength"):
        validate_domain_catalog(tmp_path)


def test_domain_catalog_rejects_missing_claim_strength(tmp_path) -> None:
    target = _copy_domain_catalog(tmp_path)
    claims = (
        (target / "claims.yaml")
        .read_text(encoding="utf-8")
        .replace("    claim_strength: vendor_ships_domain_surface\n", "", 1)
    )
    (target / "claims.yaml").write_text(claims, encoding="utf-8")

    with pytest.raises(SchemaError, match="claim_strength"):
        validate_domain_catalog(tmp_path)
