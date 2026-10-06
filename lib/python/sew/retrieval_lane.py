"""Retrieval lane: provider-vs-provider comparison on the raw search surface.

This lane answers a narrower question than the full harness matrix, and answers
it with tight error bars: *given the same query, what does each provider hand
back, how fast, how much of it, at what cost, and is the answer actually in
there?*

It deliberately does not involve an agent. Agent-in-the-loop task completion is
the harness lane's job; mixing the two makes provider effects and model effects
inseparable. Here every cell is one HTTP round trip, so repetitions are cheap
and the latency numbers are the ones an agent would actually feel.

Fairness rules enforced by this module:

* Retrieval depth is always pinned explicitly. Parallel defaults to `advanced`
  (~3s) while other providers default to a fast path, so comparing library
  defaults measures configuration, not capability. `--config matched` pins every
  provider to a comparable fast tier and a common excerpt budget.
* Content volume is measured pre-truncation, so a provider that ships 70k chars
  of page markdown is not scored as equal to one that ships a 1.2k highlight.
* A query that every provider misses is reported as suspect ground truth rather
  than as three provider failures.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

from .catalog import module_root
from .providers import ProviderRequest, make_provider

RETRIEVAL_PROVIDERS = ("exa", "parallel-web", "firecrawl", "brave", "tavily", "perplexity")

# Retrieval-depth tiers per provider. `matched` pins every provider to a comparable
# fast path; `default` uses each vendor's own recommended shape, which is what a
# developer gets if they follow the quickstart without tuning.
CONFIGS: dict[str, dict[str, dict[str, Any]]] = {
    "matched": {
        # highlights, not text: the query-relevant view is the honest peer of
        # Parallel's excerpts. See ExaProvider._request_body for why `text`
        # depresses Exa's answer rate on deep-in-page lookups.
        "exa": {"mode": "fast", "num_results": 5, "content_view": "highlights"},
        "parallel-web": {"mode": "fast", "num_results": 5, "max_chars_per_result": 4000},
        # Firecrawl exposes no retrieval-depth selector and no per-result char
        # cap on search; the scrape-backed design returns whole pages. That gap
        # is itself a finding, recorded rather than papered over.
        "firecrawl": {"num_results": 5},
        # Brave has no depth selector either; extra_snippets is its only
        # excerpt knob and the adapter turns it on everywhere.
        "brave": {"num_results": 5},
        # `fast` maps to Tavily's `basic` depth -- its latency-matched peer.
        "tavily": {"mode": "fast", "num_results": 5},
        # Perplexity's documented `fast` search type, capped per page like
        # Parallel (4000 chars ~= 1000 tokens, the API's own unit).
        "perplexity": {"mode": "fast", "num_results": 5, "max_chars_per_result": 4000},
    },
    "exa-text-view": {
        # Control config: identical to `matched` except Exa serves its
        # positional `text` view. Isolates content-view choice from index
        # quality -- the difference between this and `matched` is attributable
        # to the view alone, since the index and query set are unchanged.
        "exa": {
            "mode": "fast",
            "num_results": 5,
            "content_view": "text",
            "max_chars_per_result": 4000,
        },
        "parallel-web": {"mode": "fast", "num_results": 5, "max_chars_per_result": 4000},
        "firecrawl": {"num_results": 5},
        "brave": {"num_results": 5},
        "tavily": {"mode": "fast", "num_results": 5},
        "perplexity": {"mode": "fast", "num_results": 5, "max_chars_per_result": 4000},
    },
    "default": {
        "exa": {"mode": "auto", "num_results": 5, "content_view": "highlights"},
        "parallel-web": {"mode": "advanced", "num_results": 5},
        "firecrawl": {"num_results": 5},
        # Both quickstarts: Brave's plain web search, Tavily's `basic` depth.
        "brave": {"num_results": 5},
        "tavily": {"num_results": 5},
        # The quickstart shape: standard `web` search, default page content.
        "perplexity": {"num_results": 5},
    },
}


class RetrievalLaneError(RuntimeError):
    """Raised when the retrieval lane cannot run as configured."""


def _bounded_exponential_delay(attempt: int, *, base_seconds: float, max_seconds: float) -> float:
    from .backoff import bounded_exponential_delay

    return bounded_exponential_delay(
        attempt,
        base_seconds=base_seconds,
        max_seconds=max_seconds,
    )


@dataclass
class CellResult:
    query_id: str
    query_class: str
    provider_id: str
    repetition: int
    status: str
    latency_ms: float
    result_count: int
    content_chars: int
    answer_found: bool | None
    gold_hit: bool | None
    gold_reciprocal_rank: float | None
    fresh_source_count: int | None
    provider_cost_usd: float | None
    provider_units: dict[str, Any] = field(default_factory=dict)
    error_class: str | None = None
    retries: int = 0
    matched_urls: list[str] = field(default_factory=list)
    top_urls: list[str] = field(default_factory=list)
    started_at: str = ""


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    import yaml

    target = path or module_root() / "catalogs" / "retrieval" / "queries.yaml"
    if not target.exists():
        raise RetrievalLaneError(f"retrieval catalog not found: {target}")
    data = yaml.safe_load(target.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise RetrievalLaneError(f"malformed retrieval catalog: {target}")
    return data


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def domain_matches(url: str, domains: Sequence[str]) -> bool:
    host = host_of(url)
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def score_cell(query: dict[str, Any], sources: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Score one provider response against a query's declared ground truth."""
    patterns = [re.compile(p, re.IGNORECASE) for p in (query.get("answer_patterns") or [])]
    gold = query.get("gold_domains") or []

    answer_found: bool | None = None
    matched_urls: list[str] = []
    if patterns:
        answer_found = False
        for src in sources:
            blob = f"{src.get('title', '')}\n{src.get('snippet', '')}"
            if any(p.search(blob) for p in patterns):
                answer_found = True
                matched_urls.append(src.get("url", ""))

    gold_hit: bool | None = None
    rr: float | None = None
    if gold:
        gold_hit = False
        for rank, src in enumerate(sources, start=1):
            if domain_matches(src.get("url", ""), gold):
                gold_hit = True
                rr = 1.0 / rank
                break
        if rr is None:
            rr = 0.0

    fresh_count: int | None = None
    window = query.get("freshness_window_days")
    if window:
        cutoff = datetime.now(UTC) - timedelta(days=int(window))
        fresh_count = 0
        for src in sources:
            published = src.get("published_at")
            if not published:
                continue
            try:
                parsed = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            if parsed >= cutoff:
                fresh_count += 1

    return {
        "answer_found": answer_found,
        "gold_hit": gold_hit,
        "gold_reciprocal_rank": rr,
        "fresh_source_count": fresh_count,
        "matched_urls": matched_urls[:5],
    }


def cost_usd(
    provider_id: str, cost: dict[str, Any] | None, units: dict[str, Any] | None
) -> float | None:
    """Normalize provider spend to USD where the provider reports it.

    Only Exa returns a dollar figure directly. Firecrawl and Tavily report
    credits, whose dollar value depends on the account's plan tier; Parallel
    Brave and Perplexity report neither on the search response (Brave's quota
    lives only in rate-limit headers; Perplexity's flat per-request price is
    published, not returned). Those are left as None rather than guessed: an
    invented cost number is worse than an absent one.
    """
    if provider_id == "exa" and isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, (int, float)):
            return float(total)
    return None


def run_cell(
    provider_id: str,
    query: dict[str, Any],
    repetition: int,
    options: dict[str, Any],
    timeout_seconds: float,
    run_id: str,
    *,
    rate_limit_retries: int = 2,
    rate_limit_backoff_seconds: float = 5.0,
) -> CellResult:
    provider = make_provider(provider_id, live_enabled=True)
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    request = ProviderRequest(
        operation="search",
        run_id=run_id,
        query=query["query"],
        options=dict(options),
        timeout_seconds=timeout_seconds,
    )
    # A 429 is a fact about the account's plan tier and this runner's pacing,
    # not about retrieval quality. Booking it as a miss would let request
    # scheduling contaminate the answer rate, so back off and retry; only a
    # limit that survives the retries is recorded as rate_limited. The latency
    # clock restarts on the successful attempt for the same reason -- otherwise
    # the backoff sleep would be attributed to the provider.
    retries = 0
    for attempt in range(rate_limit_retries + 1):
        t0 = time.perf_counter()
        result = provider.call(request)
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if result.status != "rate_limited" or attempt == rate_limit_retries:
            break
        retries += 1
        time.sleep(
            _bounded_exponential_delay(
                attempt,
                base_seconds=rate_limit_backoff_seconds,
                max_seconds=rate_limit_backoff_seconds * (2 ** max(rate_limit_retries - 1, 0)),
            )
        )
    sources = [dict(s) for s in result.sources]
    scored = (
        score_cell(query, sources)
        if result.status == "ok"
        else {
            "answer_found": None,
            "gold_hit": None,
            "gold_reciprocal_rank": None,
            "fresh_source_count": None,
            "matched_urls": [],
        }
    )
    return CellResult(
        query_id=query["id"],
        query_class=query["query_class"],
        provider_id=provider_id,
        repetition=repetition,
        status=result.status,
        latency_ms=latency_ms,
        result_count=len(sources),
        content_chars=sum(int(s.get("content_length") or 0) for s in sources),
        provider_cost_usd=cost_usd(provider_id, result.provider_cost, result.provider_units),
        provider_units=dict(result.provider_units or {}),
        error_class=result.error_class,
        retries=retries,
        top_urls=[s.get("url", "") for s in sources[:5]],
        started_at=started,
        **scored,
    )


def wilson(successes: int, n: int) -> tuple[float, float]:
    """95% Wilson score interval; correct at the small n a benchmark produces."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963985
    p = successes / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def pct(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


def aggregate(cells: Sequence[CellResult], catalog: dict[str, Any]) -> dict[str, Any]:
    by_query = {q["id"]: q for q in catalog["queries"]}
    ok = [c for c in cells if c.status == "ok"]

    # A query no provider answers is more likely bad ground truth than a
    # simultaneous three-way provider failure. Surface it as such.
    suspect: list[str] = []
    for qid, q in by_query.items():
        if not (q.get("answer_patterns") or []):
            continue
        attempts = [c for c in ok if c.query_id == qid]
        provider_count = len({c.provider_id for c in attempts})
        if provider_count >= 2 and not any(c.answer_found for c in attempts):
            suspect.append(qid)

    def summarize(subset: Sequence[CellResult]) -> dict[str, Any]:
        lat = [c.latency_ms for c in subset]
        scorable = [c for c in subset if c.answer_found is not None and c.query_id not in suspect]
        hits = sum(1 for c in scorable if c.answer_found)
        goldable = [c for c in subset if c.gold_hit is not None]
        gold_hits = sum(1 for c in goldable if c.gold_hit)
        rrs = [c.gold_reciprocal_rank for c in goldable if c.gold_reciprocal_rank is not None]
        costs = [c.provider_cost_usd for c in subset if c.provider_cost_usd is not None]
        chars = [c.content_chars for c in subset]
        lo, hi = wilson(hits, len(scorable))
        glo, ghi = wilson(gold_hits, len(goldable)) if goldable else (0.0, 0.0)
        return {
            "n": len(subset),
            "errors": sum(1 for c in subset if c.status != "ok"),
            "rate_limited": sum(1 for c in subset if c.status == "rate_limited"),
            "retries": sum(c.retries for c in subset),
            "latency_p50_ms": round(pct(lat, 0.50), 1),
            "latency_p90_ms": round(pct(lat, 0.90), 1),
            "latency_p95_ms": round(pct(lat, 0.95), 1),
            "latency_mean_ms": round(statistics.fmean(lat), 1) if lat else 0.0,
            "answer_rate": round(hits / len(scorable), 4) if scorable else None,
            "answer_rate_ci95": [round(lo, 4), round(hi, 4)] if scorable else None,
            "answer_n": len(scorable),
            "gold_hit_rate": round(gold_hits / len(goldable), 4) if goldable else None,
            "gold_hit_rate_ci95": [round(glo, 4), round(ghi, 4)] if goldable else None,
            "gold_mrr": round(statistics.fmean(rrs), 4) if rrs else None,
            "content_chars_p50": int(pct([float(c) for c in chars], 0.50)),
            "content_chars_mean": int(statistics.fmean(chars)) if chars else 0,
            "cost_usd_mean": round(statistics.fmean(costs), 6) if costs else None,
            "cost_usd_total": round(sum(costs), 6) if costs else None,
        }

    providers = sorted({c.provider_id for c in cells})
    classes = sorted({c.query_class for c in cells})
    return {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "catalog_id": catalog.get("catalog_id"),
        "total_cells": len(cells),
        "suspect_ground_truth": sorted(suspect),
        "by_provider": {p: summarize([c for c in cells if c.provider_id == p]) for p in providers},
        "by_provider_class": {
            p: {
                k: summarize([c for c in cells if c.provider_id == p and c.query_class == k])
                for k in classes
                if any(c.provider_id == p and c.query_class == k for c in cells)
            }
            for p in providers
        },
        "per_query": {
            qid: {
                p: {
                    "answer_found": [
                        c.answer_found for c in cells if c.query_id == qid and c.provider_id == p
                    ],
                    "gold_rr": [
                        c.gold_reciprocal_rank
                        for c in cells
                        if c.query_id == qid and c.provider_id == p
                    ],
                    "latency_p50_ms": round(
                        pct(
                            [
                                c.latency_ms
                                for c in cells
                                if c.query_id == qid and c.provider_id == p
                            ],
                            0.5,
                        ),
                        1,
                    ),
                    "status": sorted(
                        {c.status for c in cells if c.query_id == qid and c.provider_id == p}
                    ),
                }
                for p in providers
            }
            for qid in by_query
            if any(c.query_id == qid for c in cells)
        },
    }


def run_lane(
    output_root: Path,
    *,
    config: str = "matched",
    repetitions: int = 3,
    providers: Sequence[str] = RETRIEVAL_PROVIDERS,
    query_ids: Iterable[str] | None = None,
    timeout_seconds: float = 90.0,
    catalog_path: Path | None = None,
    max_cells: int | None = None,
    progress: bool = True,
    pace_seconds: float = 0.0,
) -> dict[str, Any]:
    if config not in CONFIGS:
        raise RetrievalLaneError(f"unknown config {config!r}; expected one of {sorted(CONFIGS)}")
    catalog = load_catalog(catalog_path)
    queries = catalog["queries"]
    if query_ids is not None:
        wanted = set(query_ids)
        queries = [q for q in queries if q["id"] in wanted]
    if not queries:
        raise RetrievalLaneError("no queries selected")

    run_id = f"retrieval-{config}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    run_dir = output_root / run_id
    (run_dir / "cells").mkdir(parents=True, exist_ok=True)

    # Interleave provider order per repetition so a transient slowdown on one
    # vendor's edge cannot line up with one provider's whole block.
    cells: list[CellResult] = []
    plan = [(rep, q, p) for rep in range(1, repetitions + 1) for q in queries for p in providers]
    if max_cells is not None:
        plan = plan[:max_cells]

    cells_path = run_dir / "cells.jsonl"
    with cells_path.open("w", encoding="utf-8") as handle:
        for idx, (rep, query, provider_id) in enumerate(plan, start=1):
            options = CONFIGS[config].get(provider_id, {})
            try:
                cell = run_cell(
                    provider_id, query, rep, options, timeout_seconds, f"{run_id}-{idx:05d}"
                )
                if pace_seconds:
                    time.sleep(pace_seconds)
            except Exception as exc:  # a transport blowup is data, not a run-ender
                cell = CellResult(
                    query_id=query["id"],
                    query_class=query["query_class"],
                    provider_id=provider_id,
                    repetition=rep,
                    status="failed",
                    latency_ms=0.0,
                    result_count=0,
                    content_chars=0,
                    answer_found=None,
                    gold_hit=None,
                    gold_reciprocal_rank=None,
                    fresh_source_count=None,
                    provider_cost_usd=None,
                    error_class=f"runner_exception:{type(exc).__name__}: {exc}"[:200],
                )
            cells.append(cell)
            handle.write(json.dumps(asdict(cell), sort_keys=True) + "\n")
            handle.flush()
            if progress:
                mark = "ok " if cell.status == "ok" else "ERR"
                ans = {True: "A", False: "-", None: "."}[cell.answer_found]
                print(
                    f"[{idx:>4}/{len(plan)}] {mark} {provider_id:<13} {query['id']:<26} "
                    f"rep{rep} {cell.latency_ms:>7.0f}ms {ans} n={cell.result_count}",
                    flush=True,
                )

    report = aggregate(cells, catalog)
    report["run_id"] = run_id
    report["config"] = config
    report["repetitions"] = repetitions
    report["providers"] = list(providers)
    report["config_options"] = CONFIGS[config]
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    (run_dir / "report.md").write_text(render_markdown(report))
    report["run_dir"] = str(run_dir)
    return report


def _fmt(value: Any, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _rate(value: float | None, ci: list[float] | None = None) -> str:
    if value is None:
        return "n/a"
    text = f"{value * 100:.0f}%"
    if ci:
        text += f" ({ci[0] * 100:.0f}-{ci[1] * 100:.0f})"
    return text


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# SEW Retrieval Lane: {report['catalog_id']} / config={report['config']}")
    lines.append("")
    lines.append(f"- Run: `{report['run_id']}`")
    lines.append(f"- Generated: {report['generated_at']}")
    lines.append(f"- Repetitions: {report['repetitions']}  Cells: {report['total_cells']}")
    lines.append(f"- Provider options: `{json.dumps(report['config_options'], sort_keys=True)}`")
    if report["suspect_ground_truth"]:
        lines.append(
            f"- **Excluded as suspect ground truth** (no provider answered): "
            f"{', '.join(report['suspect_ground_truth'])}"
        )
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append(
        "| provider | n | err | answer rate (95% CI) | gold hit (95% CI) | gold MRR | "
        "p50 ms | p90 ms | p95 ms | content chars p50 | cost/query |"
    )
    lines.append("| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for provider, s in report["by_provider"].items():
        cost = f"${s['cost_usd_mean']:.4f}" if s["cost_usd_mean"] is not None else "unreported"
        lines.append(
            f"| {provider} | {s['n']} | {s['errors']} | "
            f"{_rate(s['answer_rate'], s['answer_rate_ci95'])} | "
            f"{_rate(s['gold_hit_rate'], s['gold_hit_rate_ci95'])} | {_fmt(s['gold_mrr'], digits=3)} | "
            f"{s['latency_p50_ms']:.0f} | {s['latency_p90_ms']:.0f} | {s['latency_p95_ms']:.0f} | "
            f"{s['content_chars_p50']:,} | {cost} |"
        )
    lines.append("")
    lines.append("## By query class")
    lines.append("")
    lines.append(
        "| provider | class | n | answer rate (95% CI) | gold MRR | p50 ms | content chars p50 |"
    )
    lines.append("| --- | --- | ---: | --- | ---: | ---: | ---: |")
    for provider, classes in report["by_provider_class"].items():
        for klass, s in classes.items():
            lines.append(
                f"| {provider} | {klass} | {s['n']} | {_rate(s['answer_rate'], s['answer_rate_ci95'])} | "
                f"{_fmt(s['gold_mrr'], digits=3)} | {s['latency_p50_ms']:.0f} | "
                f"{s['content_chars_p50']:,} |"
            )
    lines.append("")
    lines.append("## Per query")
    lines.append("")
    providers = report["providers"]
    lines.append("| query | " + " | ".join(f"{p} ans / p50" for p in providers) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in providers) + " |")
    for qid, per in report["per_query"].items():
        cells = []
        for p in providers:
            d = per.get(p, {})
            found = d.get("answer_found") or []
            hits = sum(1 for x in found if x)
            scored = sum(1 for x in found if x is not None)
            label = f"{hits}/{scored}" if scored else "n/a"
            cells.append(f"{label} / {d.get('latency_p50_ms', 0):.0f}ms")
        lines.append(f"| {qid} | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)
