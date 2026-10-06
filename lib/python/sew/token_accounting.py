import json
from typing import Any, Iterable, Mapping

from .metrics import normalize_token_usage


def account_tokens(
    *,
    session_ledger_usage: Mapping[str, Any] | None = None,
    harness_usage_rows: Iterable[Mapping[str, Any]] = (),
    transcript: Any = None,
    provider_model_usage: Mapping[str, Any] | None = None,
    prompt: str | None = None,
) -> dict[str, Any]:
    tokenizer_estimate = None
    if prompt is not None or transcript is not None:
        # Serialization lives inside the guard with the tokenizer: a failed
        # run's transcript can hold bytes or datetimes that json cannot encode,
        # and one malformed transcript must degrade to unknown token metrics,
        # not crash accounting for the whole report.
        try:
            text = prompt or ""
            if transcript:
                text += json.dumps(transcript)

            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
            est = len(encoding.encode(text, disallowed_special=()))
            tokenizer_estimate = {
                "input": est,
                "output": 0,
                "total_billable": est,
            }
        except Exception:
            pass

    result = normalize_token_usage(
        session_ledger_usage=session_ledger_usage,
        harness_usage_rows=harness_usage_rows,
        transcript=transcript,
        provider_model_usage=provider_model_usage,
        tokenizer_estimate=tokenizer_estimate,
    )

    # Copy before adjusting: never subtract from a mapping the normaliser may
    # share with its caller, where a second pass would double-subtract.
    result = dict(result) if result else result

    # "cached tokens are not double-counted into input"
    if result and result.get("cached_input") and result.get("input"):
        # Keep input as uncached tokens while reporting cached_input separately.
        if result["input"] >= result["cached_input"]:
            result["input"] -= result["cached_input"]

    return result
