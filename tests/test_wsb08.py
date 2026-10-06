import math

from sew.retrieval_lane import CellResult, aggregate, render_markdown, wilson


def test_wilson_reference_implementation() -> None:
    lo, hi = wilson(8, 10)
    assert math.isclose(lo, 0.490153, abs_tol=1e-4)
    assert math.isclose(hi, 0.943261, abs_tol=1e-4)

    lo, hi = wilson(1, 1)
    assert math.isclose(lo, 0.2065, abs_tol=1e-4)
    assert math.isclose(hi, 1.0, abs_tol=1e-4)

    lo, hi = wilson(0, 0)
    assert (lo, hi) == (0.0, 0.0)


def test_retrieval_gold_hit_rate_carries_wilson_interval() -> None:
    catalog = {
        "catalog_id": "gold-hit-fixture",
        "queries": [
            {"id": "hit", "query_class": "fact_lookup", "answer_patterns": ["answer"]},
            {"id": "miss", "query_class": "fact_lookup", "answer_patterns": ["answer"]},
        ],
    }
    cells = [
        CellResult(
            query_id=query_id,
            query_class="fact_lookup",
            provider_id="exa",
            repetition=1,
            status="ok",
            latency_ms=100.0,
            result_count=1,
            content_chars=100,
            answer_found=hit,
            gold_hit=hit,
            gold_reciprocal_rank=1.0 if hit else 0.0,
            fresh_source_count=None,
            provider_cost_usd=None,
        )
        for query_id, hit in (("hit", True), ("miss", False))
    ]

    report = aggregate(cells, catalog)
    summary = report["by_provider"]["exa"]
    assert summary["gold_hit_rate"] == 0.5
    assert summary["gold_hit_rate_ci95"] == [0.0945, 0.9055]
    assert report["by_provider_class"]["exa"]["fact_lookup"]["gold_hit_rate_ci95"] == [
        0.0945,
        0.9055,
    ]

    report.update(
        config="matched",
        run_id="gold-hit-fixture",
        repetitions=1,
        config_options={},
        providers=["exa"],
    )
    markdown = render_markdown(report)
    assert "gold hit (95% CI)" in markdown
    assert "50% (9-91)" in markdown
