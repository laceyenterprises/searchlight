"""Grade an insight run: extract claims, cluster them, verify each on its page, value clusters.

Every model step is a plain callable taking one JSON payload and returning one
JSON object (``HarnessJudgeTransport(contract="json")`` live, a stub in tests).
Each artifact is a file under ``grading/`` carrying a digest of its inputs, so a
rerun redoes only what is missing or stale.

Blinding: no payload sent to a judging step carries an arm label, and
``assert_blind`` refuses one that does. The value judge additionally sees the
tested vendors' brand names redacted (unless the question or reader context
names them), clusters in a seeded shuffle, and no URLs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..judge import BlindingError
from .catalog import _STOPWORDS, InsightError, load_set
from .run import now, read_json, write_json

GRADE_VERSION = "insight-grade-v1"
MAX_CLAIMS = 25
ANSWER_CHARS = 40_000
PAGE_CHARS = 60_000
BASELINE_CHARS = 60_000
PAIR_JACCARD = 0.2
MAX_PAIRS = 400
BATCH = 50

Model = Callable[[Mapping[str, Any]], Mapping[str, Any]]

EXTRACT = (
    "Turn the retrieved material below into atomic claims that help answer the question. "
    "One claim states one specific fact, design detail, number, date, result or failure mode, "
    "self-contained (name the subject; no pronouns). Use only what the material states; never "
    "add knowledge of your own. Skip navigation, marketing adjectives and generic statements. "
    "Give each claim the number of the source that states it, or null if the material states "
    f"it without a source. At most {MAX_CLAIMS} claims, the most specific first."
)
EQUIVALENT = (
    "For each pair, answer 1 if claims a and b state the same fact (paraphrases, or one is a "
    "subset of the other with the same key number or detail), otherwise 0. Related but "
    "different facts are 0."
)
VERIFY = (
    "For each claim, answer 1 only if the page text states it (a faithful paraphrase counts), "
    "otherwise 0. A claim the page contradicts, or does not mention, is 0. Use only the page "
    "text, never your own knowledge."
)
VALUE = (
    "Value each finding for the reader described in reader_context, as an answer to the "
    "question. 3: a specific fact or measured result that could change an architecture "
    "decision. 2: a specific, new design detail. 1: corroborating or minor. 0: generic, "
    "trivia, marketing, or already stated in the baseline. Findings are verified on their "
    "sources; judge only their value. Give a one-sentence reason for each."
)
# Brand names a value judge could read as the arm's own voice ("Exa found ...").
BRANDS = {
    "exa": ("Exa", "exa.ai", "Websets"),
    "parallel": ("Parallel Web Systems", "Parallel Web", "Parallel AI", "parallel.ai"),
    "tavily": ("Tavily",),
    "firecrawl": ("Firecrawl",),
    "brave": ("Brave Search", "Brave Answers"),
    "perplexity": ("Perplexity",),
}
_WORD = re.compile(r"[a-z0-9][a-z0-9.+-]*")
_URL = re.compile(r"https?://[^\s\"'<>\]\)}]+")


def _sha(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def content_tokens(text: str) -> set[str]:
    return {w.strip(".") for w in _WORD.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1}


def assert_blind(payload: Mapping[str, Any], arm_ids: Iterable[str]) -> None:
    """Refuse a judging payload that names an arm or carries an arm-shaped key."""
    text = json.dumps(payload, ensure_ascii=False)
    leaks = [a for a in arm_ids if re.search(rf"(?<![\w.]){re.escape(a)}(?![\w])", text)]
    leaks += [k for k in ('"arm"', '"arm_id"', '"provider"', '"provider_id"') if k in text]
    if leaks:
        raise BlindingError(f"judge payload names an arm: {', '.join(sorted(set(leaks)))}")


def redact_brands(text: str, arms: Iterable[str], keep: str) -> str:
    for arm in arms:
        for brand in BRANDS.get(arm, ()):
            if brand.lower() not in keep.lower():
                text = re.sub(rf"(?<![\w.]){re.escape(brand)}(?![\w])", "[search vendor]", text)
    return text


def _ask(model: Model, payload: Mapping[str, Any], check: Callable[[Any], Any]) -> Any:
    """One model call, retried once on a malformed reply."""
    error: Exception | None = None
    for _ in range(2):
        try:
            return check(model(copy.deepcopy(dict(payload))))
        except (InsightError, ValueError, KeyError, TypeError) as exc:
            error = exc
    raise InsightError(f"model reply unusable: {type(error).__name__}: {error}")


def _binary_map(key: str, labels: Sequence[str]) -> Callable[[Any], dict[str, int]]:
    def check(reply: Any) -> dict[str, int]:
        got = reply[key]
        out = {label: got[label] for label in labels}
        if any(v not in (0, 1) or isinstance(v, bool) for v in out.values()):
            raise InsightError(f"{key} values must be 0 or 1")
        return out

    return check


def _batches(items: Sequence[Any], size: int = BATCH) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# --------------------------------------------------------------------------
# 1. Extract
# --------------------------------------------------------------------------


def _drop_warnings(value: Any) -> Any:
    """Vendor ``warnings`` are call metadata, never extractor input (Exa 3.0 asks this)."""
    if isinstance(value, Mapping):
        return {k: _drop_warnings(v) for k, v in value.items() if k != "warnings"}
    if isinstance(value, list):
        return [_drop_warnings(v) for v in value]
    return value


def _answer_material(record: Mapping[str, Any]) -> tuple[str, list[str]]:
    response = record.get("response")
    body = response if isinstance(response, str) else json.dumps(
        _drop_warnings(response), ensure_ascii=False, default=str)
    urls = list(dict.fromkeys(u.rstrip(".,;:") for u in _URL.findall(body)))[:80]
    return body[:ANSWER_CHARS], urls


def extract_cell(model: Model, question: str, record: Mapping[str, Any]) -> list[dict[str, Any]]:
    if record.get("items"):
        sources = [{"n": i + 1, "url": it["url"], "title": it.get("title", ""), "text": it["text"],
                    **({"page_text": it["page_text"]} if it.get("page_text") else {})}
                   for i, it in enumerate(record["items"])]
        answer = None
    else:
        if record.get("response") in (None, {}, [], ""):
            return []
        answer, urls = _answer_material(record)
        sources = [{"n": i + 1, "url": u} for i, u in enumerate(urls)]
    payload = {
        "instructions": EXTRACT,
        "question": question,
        "sources": sources,
        "answer": answer,
        "vendor_notices": record.get("notices"),
        "response_contract": {"claims": [{"text": "<one atomic claim>", "source": "<n or null>"}]},
    }

    def check(reply: Any) -> list[dict[str, Any]]:
        claims = []
        for c in reply["claims"][:MAX_CLAIMS]:
            text, n = str(c["text"]).strip()[:600], c.get("source")
            if not text:
                continue
            ok = isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= len(sources)
            claims.append({"text": text, "url": sources[n - 1]["url"] if ok else None})
        return claims

    return _ask(model, payload, check)


# --------------------------------------------------------------------------
# 2. Cluster
# --------------------------------------------------------------------------


def candidate_pairs(claims: Sequence[Mapping[str, Any]]) -> list[tuple[int, int, float]]:
    tokens = [content_tokens(c["text"]) for c in claims]
    pairs = []
    for i in range(len(claims)):
        for j in range(i + 1, len(claims)):
            union = tokens[i] | tokens[j]
            score = len(tokens[i] & tokens[j]) / len(union) if union else 0.0
            if score >= PAIR_JACCARD:
                pairs.append((i, j, score))
    pairs.sort(key=lambda p: (-p[2], p[0], p[1]))
    return pairs[:MAX_PAIRS]


def cluster_claims(
    model: Model, claims: Sequence[Mapping[str, Any]], *, rng: random.Random, arm_ids: Sequence[str]
) -> tuple[list[list[str]], int]:
    """Union-find over exact duplicates and model-confirmed equivalent pairs."""
    parent = list(range(len(claims)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen: dict[str, int] = {}
    for i, c in enumerate(claims):
        key = " ".join(sorted(content_tokens(c["text"])))
        if key in seen:
            parent[find(i)] = find(seen[key])
        seen.setdefault(key, i)
    pairs, asked = [], set()
    for i, j, _ in candidate_pairs(claims):
        roots = tuple(sorted((find(i), find(j))))
        if roots[0] != roots[1] and roots not in asked:  # one question per pair of groups
            asked.add(roots)
            pairs.append((i, j))
    rng.shuffle(pairs)
    for batch in _batches(pairs):
        labels = [f"p{n + 1}" for n in range(len(batch))]
        payload = {
            "instructions": EQUIVALENT,
            "pairs": [{"p": lab, "a": claims[i]["text"], "b": claims[j]["text"]}
                      for lab, (i, j) in zip(labels, batch)],
            "response_contract": {"same": {lab: "<0 or 1>" for lab in labels}},
        }
        assert_blind(payload, arm_ids)
        same = _ask(model, payload, _binary_map("same", labels))
        for lab, (i, j) in zip(labels, batch):
            if same[lab]:
                parent[find(j)] = find(i)
    groups: dict[int, list[str]] = {}
    for i, c in enumerate(claims):
        groups.setdefault(find(i), []).append(c["id"])
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0]), len(pairs)


# --------------------------------------------------------------------------
# 3. Verify
# --------------------------------------------------------------------------


def excerpt(text: str, claims: Sequence[str], limit: int = PAGE_CHARS) -> str:
    """The page, or its paragraphs that share the most terms with the claims."""
    if len(text) <= limit:
        return text
    paras = [p for p in text.split("\n") if p.strip()]
    terms = set().union(*(content_tokens(c) for c in claims))
    order = sorted(range(len(paras)), key=lambda i: (-len(content_tokens(paras[i]) & terms), i))
    keep: set[int] = set()
    used = 0
    for i in order:
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(paras) and j not in keep and used + len(paras[j]) < limit:
                keep.add(j)
                used += len(paras[j]) + 1
        if used >= limit * 0.95:
            break
    return "\n".join(paras[i] for i in sorted(keep))


def verify_page(
    model: Model, page: str, texts: Sequence[str], *, rng: random.Random, arm_ids: Sequence[str]
) -> dict[str, int]:
    order = list(texts)
    rng.shuffle(order)
    verdicts: dict[str, int] = {}
    for batch in _batches(order, 40):
        labels = [f"c{n + 1}" for n in range(len(batch))]
        payload = {
            "instructions": VERIFY,
            "page": excerpt(page, batch),
            "claims": [{"c": lab, "text": t} for lab, t in zip(labels, batch)],
            "response_contract": {"supported": {lab: "<0 or 1>" for lab in labels}},
        }
        assert_blind(payload, arm_ids)
        got = _ask(model, payload, _binary_map("supported", labels))
        verdicts.update({t: got[lab] for lab, t in zip(labels, batch)})
    return verdicts


# --------------------------------------------------------------------------
# 4. Value
# --------------------------------------------------------------------------


def value_findings(
    model: Model,
    findings: Mapping[str, str],
    *,
    question: str,
    context: str,
    baseline: str | None,
    rng: random.Random,
    arm_ids: Sequence[str],
) -> dict[str, dict[str, Any]]:
    """Blind 0-3 values for cluster texts, keyed by cluster id; the order sent is shuffled."""
    order = sorted(findings)
    rng.shuffle(order)
    arms = {a.split(".")[0] for a in arm_ids}
    keep = f"{question}\n{context}"
    values: dict[str, dict[str, Any]] = {}
    for batch in _batches(order):
        labels = [f"k{n + 1}" for n in range(len(batch))]
        payload = {
            "instructions": VALUE,
            "question": question,
            "reader_context": context,
            "baseline": baseline[:BASELINE_CHARS] if baseline else None,
            "findings": [{"k": lab, "text": redact_brands(findings[cid], arms, keep)}
                         for lab, cid in zip(labels, batch)],
            "response_contract": {"value": {lab: {"score": "<0-3>", "reason": "<one sentence>"}
                                            for lab in labels}},
        }
        assert_blind(payload, arm_ids)

        def check(reply: Any, labels: list[str] = labels) -> dict[str, dict[str, Any]]:
            out = {}
            for lab in labels:
                score, reason = reply["value"][lab]["score"], reply["value"][lab]["reason"]
                if isinstance(score, bool) or score not in (0, 1, 2, 3) or not str(reason).strip():
                    raise InsightError(f"value for {lab} must be 0-3 with a reason")
                out[lab] = {"score": int(score), "reason": str(reason).strip()[:400]}
            return out

        got = _ask(model, payload, check)
        values.update({cid: {**got[lab], "position": order.index(cid)}
                       for lab, cid in zip(labels, batch)})
    return values


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def _describe(model: Model) -> dict[str, Any]:
    return {"harness": getattr(model, "harness_id", None),
            "model": getattr(model, "model_id", None) or "harness default",
            "kind": type(model).__name__}


def load_claims(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Extracted claims per question, each with a stable id ``<arm_id>#<n>``."""
    out: dict[str, list[dict[str, Any]]] = {}
    for path in sorted((run_dir / "grading" / "extract").glob("*/*.json")):
        doc = read_json(path)
        out.setdefault(doc["question_id"], []).extend(
            {"id": f"{doc['arm_id']}#{n + 1}", "arm_id": doc["arm_id"], **c}
            for n, c in enumerate(doc["claims"])
        )
    return out


def grade_run(
    run_dir: Path,
    *,
    extractor: Callable[[], Model],
    judge: Callable[[], Model],
    capture: Callable[[str], str] | None = None,
    seed: int | None = None,
    workers: int = 4,
    set_root: Path | None = None,
    progress: Callable[[str], None] | None = print,
) -> dict[str, Any]:
    """Run the four steps; ``extractor``/``judge`` build one model per worker thread."""
    manifest = read_json(run_dir / "manifest.json")
    iset = load_set(manifest["set_id"], set_root)
    if iset.sha256 != manifest["set_sha256"]:
        raise InsightError(f"set {iset.set_id} changed since this run; grade with the old set")
    gdir = run_dir / "grading"
    meta_path = gdir / "grading.json"
    meta = read_json(meta_path) if meta_path.is_file() else {
        "version": GRADE_VERSION, "seed": seed if seed is not None else random.SystemRandom().getrandbits(32),
        "created_at": now()}
    if seed is not None and seed != meta["seed"]:
        raise InsightError(f"run was graded with seed {meta['seed']}; regrade in a fresh copy")
    local = threading.local()
    arm_ids = sorted(manifest["profiles"])
    errors: list[str] = []
    stats = {"model_calls": 0}

    def model(kind: str) -> Model:
        if not hasattr(local, kind):
            setattr(local, kind, (extractor if kind == "extractor" else judge)())
            meta[kind] = _describe(getattr(local, kind))
        stats["model_calls"] += 1
        return getattr(local, kind)

    def rng(*parts: str) -> random.Random:
        return random.Random(f"{meta['seed']}:" + ":".join(parts))

    def parallel(fn: Callable[[Any], None], jobs: Sequence[Any]) -> None:
        def safe(job: Any) -> None:
            try:
                fn(job)
            except (InsightError, BlindingError) as exc:
                label = job if isinstance(job, str) else f"{job.parent.name}/{job.stem}"
                errors.append(f"{label}: {exc}")
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(safe, jobs))

    # 1. extract
    def extract(path: Path) -> None:
        record = read_json(path)
        out = gdir / "extract" / record["question_id"] / f"{record['arm_id']}.json"
        digest = _sha(record)
        if out.is_file() and read_json(out).get("cell_sha") == digest:
            return
        question = iset.question(record["question_id"])["text"]
        claims = extract_cell(model("extractor"), question, record)
        write_json(out, {"question_id": record["question_id"], "arm_id": record["arm_id"],
                         "cell_sha": digest, "claims": claims})

    cells = [p for p in sorted((run_dir / "cells").glob("*/*.json")) if read_json(p).get("status") == "ok"]
    parallel(extract, cells)
    claims = load_claims(run_dir)

    # 2. cluster
    def cluster(qid: str) -> None:
        out = gdir / "clusters" / f"{qid}.json"
        digest = _sha([(c["id"], c["text"]) for c in claims[qid]])
        if out.is_file() and read_json(out).get("digest") == digest:
            return
        groups, checked = cluster_claims(model("judge"), claims[qid], rng=rng(qid, "pairs"),
                                         arm_ids=arm_ids)
        write_json(out, {"digest": digest, "pairs_checked": checked,
                         "clusters": [{"id": f"{qid}-k{n + 1}", "members": g}
                                      for n, g in enumerate(groups)]})

    parallel(cluster, sorted(claims))

    # 3. verify, once per cited page
    by_url: dict[str, set[str]] = {}
    for qclaims in claims.values():
        for c in qclaims:
            if c.get("url"):
                by_url.setdefault(c["url"], set()).add(c["text"])

    def verify(url: str) -> None:
        key = hashlib.sha256(url.encode()).hexdigest()[:20]
        out, page_path = gdir / "verify" / f"{key}.json", gdir / "pages" / f"{key}.json"
        texts = sorted(by_url[url])
        digest = _sha(texts)
        if out.is_file() and read_json(out).get("digest") == digest:
            return
        if not page_path.is_file():
            try:
                text = (capture or _default_capture)(url)
                page = {"url": url, "status": "captured" if text.strip() else "empty", "text": text}
            except Exception as exc:  # noqa: BLE001 - an unreadable page is evidence, not a crash
                page = {"url": url, "status": "uncapturable", "text": None, "error": type(exc).__name__}
            write_json(page_path, page)
        page = read_json(page_path)
        verdicts = (verify_page(model("judge"), page["text"], texts, rng=rng(key), arm_ids=arm_ids)
                    if page["status"] == "captured" else {t: None for t in texts})
        write_json(out, {"url": url, "digest": digest, "capture": page["status"], "verdicts": verdicts})

    parallel(verify, sorted(by_url))

    # 4. value the clusters that have at least one supported claim
    support = supported_claims(run_dir, claims)

    def value(qid: str) -> None:
        path = gdir / "clusters" / f"{qid}.json"
        if not path.is_file():
            return
        texts = {}
        for cl in read_json(path)["clusters"]:
            ok = [c["text"] for c in claims[qid] if c["id"] in cl["members"] and support.get(c["id"]) == 1]
            if ok:
                texts[cl["id"]] = sorted(ok, key=lambda t: (-len(t), t))[0]
        out = gdir / "judge" / f"{qid}.json"
        digest = _sha([texts, iset.context, iset.baseline])
        if out.is_file() and read_json(out).get("digest") == digest:
            return
        values = value_findings(model("judge"), texts, question=iset.question(qid)["text"],
                                context=iset.context, baseline=iset.baseline,
                                rng=rng(qid, "value"), arm_ids=arm_ids) if texts else {}
        write_json(out, {"digest": digest, "values": values})

    parallel(value, sorted(claims))
    meta["updated_at"] = now()
    write_json(meta_path, meta)
    summary = {"run_dir": str(run_dir), "seed": meta["seed"], "cells_graded": len(cells),
               "claims": sum(len(v) for v in claims.values()), "pages": len(by_url),
               "model_calls": stats["model_calls"], "errors": errors}
    if progress:
        progress(json.dumps(summary, indent=2))
    return summary


def supported_claims(run_dir: Path, claims: Mapping[str, list[Mapping[str, Any]]]) -> dict[str, int | None]:
    """claim id -> 1 supported, 0 unsupported, None unverifiable; uncited claims are absent."""
    verdicts: dict[tuple[str, str], int | None] = {}
    for path in (run_dir / "grading" / "verify").glob("*.json"):
        doc = read_json(path)
        for text, verdict in doc["verdicts"].items():
            verdicts[(doc["url"], text)] = verdict
    return {c["id"]: verdicts.get((c["url"], c["text"]))
            for qclaims in claims.values() for c in qclaims if c.get("url")}


def _default_capture(url: str) -> str:
    from ..gap.calibrate import _capture_source

    return _capture_source(url)
