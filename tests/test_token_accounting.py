from sew.token_accounting import account_tokens


def test_source_tiers_exercised(monkeypatch):
    res = account_tokens(session_ledger_usage={"input": 10, "output": 20})
    assert res["source_kind"] == "session_ledger"
    assert res["input"] == 10

    res = account_tokens(harness_usage_rows=[{"input": 15, "output": 25}])
    assert res["source_kind"] == "harness_usage_rows"
    assert res["input"] == 15

    res = account_tokens(transcript=[{"usage": {"input": 20, "output": 30}}])
    assert res["source_kind"] == "harness_transcript_metadata"
    assert res["input"] == 20

    res = account_tokens(provider_model_usage={"input": 25, "output": 35})
    assert res["source_kind"] == "provider_model_server_usage"
    assert res["input"] == 25

    import sys
    from unittest.mock import MagicMock

    tiktoken = MagicMock()
    encoding = MagicMock()
    encoding.encode.return_value = [1, 2, 3, 4]  # mock 4 tokens
    tiktoken.get_encoding.return_value = encoding
    monkeypatch.setitem(sys.modules, "tiktoken", tiktoken)

    res = account_tokens(prompt="abcd", transcript=[])
    assert res["source_kind"] == "tokenizer_estimate"
    assert res["input"] == 4


def test_tokenizer_load_failure_falls_back_to_unknown(monkeypatch):
    import sys
    from unittest.mock import MagicMock

    tiktoken = MagicMock()
    tiktoken.get_encoding.side_effect = OSError("offline tokenizer cache")
    monkeypatch.setitem(sys.modules, "tiktoken", tiktoken)

    res = account_tokens(prompt="abcd")
    assert res["accounting_source"] == "unknown"
    assert res["input"] is None


def test_missing_usage_is_unknown():
    res = account_tokens()
    assert res["accounting_source"] == "unknown"
    assert res["input"] is None
    assert res["output"] is None


def test_provenance_present():
    res = account_tokens(session_ledger_usage={"input": 10})
    assert "source_kind" in res
    assert res["source_kind"] == "session_ledger"
    assert res["accounting_source"] == "measured"


def test_cached_tokens_not_double_counted():
    res = account_tokens(session_ledger_usage={"input": 100, "cached_input": 40})
    assert res["input"] == 60
    assert res["cached_input"] == 40


def test_an_unserializable_transcript_degrades_instead_of_crashing():
    import datetime

    transcript = [{"note": b"\x00raw", "at": datetime.datetime(2026, 9, 25)}]

    res = account_tokens(prompt="abcd", transcript=transcript)

    assert res["accounting_source"] in {"unknown", "estimated", "measured"}


def test_cached_input_adjustment_never_mutates_the_callers_mapping(monkeypatch):
    from sew import token_accounting

    shared = {
        "input": 100,
        "cached_input": 40,
        "output": 5,
        "source_kind": "session_ledger",
        "accounting_source": "measured",
    }
    monkeypatch.setattr(token_accounting, "normalize_token_usage", lambda **kwargs: shared)

    first = account_tokens(session_ledger_usage={"input": 100})
    second = account_tokens(session_ledger_usage={"input": 100})

    assert first["input"] == 60
    assert second["input"] == 60  # no double subtraction through a shared reference
    assert shared["input"] == 100
