from __future__ import annotations

import io
import json
import socket
import urllib.error
from typing import Any, Mapping

import pytest

from conftest import FIXTURE_ROOT

from sew.providers import (
    CredentialResolver,
    HttpResponse,
    ProviderRequest,
    UrllibTransport,
    classify_http_status,
    content_hash,
    make_provider,
    stable_call_id,
    utc_now,
)


class StubTransport:
    def __init__(
        self,
        response: HttpResponse | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.response = response or HttpResponse(200, {"results": []})
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": dict(json_body or {}),
                "timeout_seconds": timeout_seconds,
            }
        )
        if self.error:
            raise self.error
        return self.response


class RecordingBody(io.BytesIO):
    def __init__(self, payload: bytes) -> None:
        super().__init__(payload)
        self.read_sizes: list[int | None] = []

    def read(self, size: int | None = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(size)


def test_capability_negotiation_marks_unsupported_operation_not_applicable() -> None:
    provider = make_provider(
        "exa",
        credential_resolver=CredentialResolver(environ={"SEW_EXA_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(),
    )

    result = provider.crawl("https://example.com", run_id="run-unsupported")

    assert result.status == "not_applicable"
    assert result.error_class == "unsupported_operation"
    assert result.schema_provider_call()["status"] == "not_applicable"
    assert provider.capabilities.supports("search")
    assert not provider.capabilities.supports("crawl")


def test_fixture_provider_replays_recorded_provider_call_and_sources() -> None:
    provider = make_provider("fixture", fixture_run_dir=FIXTURE_ROOT / "success")

    result = provider.search("ignored in fixture replay", run_id="fixture-run")

    assert result.status == "ok"
    assert result.call_record["call_id"] == "fixture-success-search-1"
    assert result.sources[0]["source_id"] == "src-success-release-note"
    assert result.schema_sources()[0]["redaction"] == {
        "state": "sanitized_excerpt",
        "raw_content_included": False,
    }


def test_fixture_provider_replays_cost_metadata(tmp_path) -> None:
    calls_dir = tmp_path / "provider-calls"
    calls_dir.mkdir(parents=True)
    (calls_dir / "search.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "call_id": "fixture-search-cost",
                "run_id": "fixture-run",
                "provider_id": "fixture",
                "operation": "search",
                "status": "ok",
                "started_at": "2026-01-01T00:00:00Z",
                "ended_at": "2026-01-01T00:00:01Z",
                "request": {"operation": "search", "redacted": True},
                "response": {
                    "provider_units": {"requests": 1},
                    "provider_cost": {"currency": "USD", "amount": 0.01},
                },
                "normalized_source_refs": [],
                "retry_count": 0,
            }
        )
    )
    provider = make_provider("fixture", fixture_run_dir=tmp_path)

    result = provider.search("fixture cost", run_id="fixture-run")

    assert result.provider_units == {"requests": 1}
    assert result.provider_cost == {"currency": "USD", "amount": 0.01}


def test_fixture_provider_consumes_replayed_calls_sequentially(tmp_path) -> None:
    calls_dir = tmp_path / "provider-calls"
    calls_dir.mkdir(parents=True)
    for idx in range(2):
        (calls_dir / f"{idx + 1:03d}.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "call_id": f"fixture-search-{idx + 1}",
                    "run_id": "fixture-run",
                    "provider_id": "fixture",
                    "operation": "search",
                    "status": "ok",
                    "started_at": "2026-01-01T00:00:00Z",
                    "ended_at": "2026-01-01T00:00:01Z",
                    "request": {"operation": "search", "redacted": True},
                    "response": {},
                    "normalized_source_refs": [],
                    "retry_count": 0,
                }
            )
        )
    provider = make_provider("fixture", fixture_run_dir=tmp_path)

    first = provider.search("first", run_id="fixture-run")
    second = provider.search("second", run_id="fixture-run")
    exhausted = provider.search("third", run_id="fixture-run")

    assert first.call_record["call_id"] == "fixture-search-1"
    assert second.call_record["call_id"] == "fixture-search-2"
    assert exhausted.status == "not_applicable"
    assert exhausted.error_class == "fixture_missing_operation"


@pytest.mark.parametrize(
    ("http_status", "expected"),
    [
        (200, "ok"),
        (429, "rate_limited"),
        (403, "refused"),
        (408, "timeout"),
        (500, "failed"),
    ],
)
def test_http_status_classification(http_status: int, expected: str) -> None:
    assert classify_http_status(http_status) == expected


def test_timeout_classification_from_transport() -> None:
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(error=socket.timeout("slow")),
    )

    result = provider.search("slow query", run_id="run-timeout")

    assert result.status == "timeout"
    assert result.schema_provider_call()["status"] == "timeout"
    assert result.error_class == "timeout"


def test_timeout_classification_from_urllib_wrapped_socket_timeout() -> None:
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(error=urllib.error.URLError(socket.timeout("slow"))),
    )

    result = provider.search("slow query", run_id="run-wrapped-timeout")

    assert result.status == "timeout"
    assert result.schema_provider_call()["status"] == "timeout"
    assert result.error_class == "timeout"


def test_http_error_body_is_preserved_in_call_record() -> None:
    http_error = urllib.error.HTTPError(
        "https://api.example.test",
        400,
        "Bad Request",
        hdrs={},
        fp=io.BytesIO(b'{"error":"bad request"}'),
    )
    provider = make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(error=http_error),
    )

    result = provider.search("bad payload", run_id="run-http-error")

    assert result.status == "failed"
    assert result.call_record["response"] == {
        "http_status": 400,
        "error_body": '{"error":"bad request"}',
    }


def test_http_error_body_reads_bounded_prefix() -> None:
    body = RecordingBody(b"x" * 5000)
    http_error = urllib.error.HTTPError(
        "https://api.example.test",
        500,
        "Server Error",
        hdrs={},
        fp=body,
    )
    provider = make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(error=http_error),
    )

    result = provider.search("bad payload", run_id="run-http-error")

    assert body.read_sizes == [4096]
    assert len(result.call_record["response"]["error_body"]) == 1024


def test_urllib_transport_sets_default_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_headers: dict[str, str | None] = {}

    class FakeResponse:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int | None = -1) -> bytes:
            return b'{"results":[]}'

    def fake_urlopen(req: Any, *, timeout: float) -> FakeResponse:
        captured_headers["user_agent"] = req.get_header("User-agent")
        captured_headers["timeout"] = str(timeout)
        return FakeResponse()

    monkeypatch.setattr("sew.providers.urlrequest.urlopen", fake_urlopen)

    response = UrllibTransport().request(
        "POST",
        "https://api.example.test/search",
        headers={},
        json_body={"query": "example"},
        timeout_seconds=5,
    )

    assert response.status_code == 200
    assert captured_headers == {
        "user_agent": "SearchEvaluationWorkbench/1.0",
        "timeout": "5",
    }


def test_urllib_transport_replaces_invalid_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int | None = -1) -> bytes:
            return b"\xffbinary-ish"

    def fake_urlopen(req: Any, *, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("sew.providers.urlrequest.urlopen", fake_urlopen)

    response = UrllibTransport().request(
        "GET",
        "https://api.example.test/binary",
        headers={},
        json_body=None,
        timeout_seconds=5,
    )

    assert response.body == "\ufffdbinary-ish"


def test_urllib_transport_reads_bounded_response_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = RecordingBody(b'{"results":[]}' + b" " * 4096)

    class FakeResponse:
        status = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, size: int | None = -1) -> bytes:
            return body.read(size)

    def fake_urlopen(req: Any, *, timeout: float) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr("sew.providers.urlrequest.urlopen", fake_urlopen)

    UrllibTransport().request(
        "GET",
        "https://api.example.test/search",
        headers={},
        json_body=None,
        timeout_seconds=5,
    )

    assert body.read_sizes == [20 * 1024 * 1024]


def test_refusal_and_rate_limit_records_conform_to_provider_call_schema() -> None:
    for http_status, expected in [(429, "rate_limited"), (401, "refused")]:
        provider = make_provider(
            "parallel-web",
            credential_resolver=CredentialResolver(environ={"SEW_PARALLEL_WEB_API_KEY": "secret"}),
            live_enabled=True,
            transport=StubTransport(HttpResponse(http_status, {"error": "classified"})),
        )

        result = provider.search("classified query", run_id=f"run-{expected}")

        assert result.status == expected
        assert result.schema_provider_call()["status"] == expected
        assert result.error_class == f"http_{http_status}"


def test_missing_secret_is_typed_unavailable_without_transport_call() -> None:
    transport = StubTransport()
    provider = make_provider(
        "exa",
        credential_resolver=CredentialResolver(environ={}),
        live_enabled=True,
        transport=transport,
    )

    result = provider.search("needs credential", run_id="run-missing-secret")

    assert result.status == "unavailable"
    assert result.error_class == "missing_credential"
    assert result.schema_provider_call()["status"] == "failed"
    assert result.call_record["response"]["credential"] == "unavailable"
    assert transport.calls == []


def test_live_smoke_is_skipped_without_explicit_flag() -> None:
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=False,
        transport=StubTransport(),
    )

    health = provider.health()
    result = provider.search("offline", run_id="run-offline")

    assert health.status == "not_applicable"
    assert "SEW_ALLOW_LIVE_PROVIDER_SMOKE" in (health.detail or "")
    assert result.status == "not_applicable"
    assert result.error_class == "live_smoke_disabled"


def test_redaction_markers_and_content_hash_on_normalized_source() -> None:
    body = {
        "results": [
            {
                "url": "https://example.com/page",
                "title": "Example Page",
                "snippet": "Bounded sanitized content.",
            }
        ],
        "usage": {"requests": 1},
        "cost": {"currency": "USD", "amount": 0.01},
    }
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(HttpResponse(200, body)),
    )

    result = provider.search("hash me", run_id="run-hash")

    source = result.schema_sources()[0]
    assert source["redaction"] == {"state": "sanitized_excerpt", "raw_content_included": False}
    assert source["content_hash"] == content_hash("Bounded sanitized content.")
    assert result.provider_units == {"requests": 1}
    assert result.provider_cost == {"currency": "USD", "amount": 0.01}


def test_live_source_ids_are_namespaced_by_call_id() -> None:
    body = {
        "results": [
            {
                "url": "https://example.com/one",
                "title": "One",
                "snippet": "first",
            },
            {
                "url": "https://example.com/two",
                "title": "Two",
                "snippet": "second",
            },
        ]
    }
    provider = make_provider(
        "exa",
        credential_resolver=CredentialResolver(environ={"SEW_EXA_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(HttpResponse(200, body)),
    )

    result = provider.search("collision guard", run_id="run-source-id")

    call_id = result.call_record["call_id"]
    assert [source["source_id"] for source in result.sources] == [
        f"{call_id}-1",
        f"{call_id}-2",
    ]
    assert result.call_record["normalized_source_refs"] == [
        f"sources/{call_id}-1.json",
        f"sources/{call_id}-2.json",
    ]


def test_title_fallback_source_has_matching_hash_and_length() -> None:
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(
            HttpResponse(
                200,
                {"results": [{"url": "https://example.com/page", "title": "Fallback Title"}]},
            )
        ),
    )

    result = provider.search("fallback", run_id="run-title-fallback")

    source = result.schema_sources()[0]
    assert source["snippet"] == "Fallback Title"
    assert source["content_hash"] == content_hash("Fallback Title")
    assert source["content_length"] == len("Fallback Title".encode("utf-8"))


def test_single_object_data_response_is_normalized_as_one_source() -> None:
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=StubTransport(
            HttpResponse(
                200,
                {"data": {"url": "https://example.com/scrape", "title": "Scraped page"}},
            )
        ),
    )

    result = provider.fetch("https://example.com/scrape", run_id="run-single-data")

    assert result.status == "ok"
    assert len(result.sources) == 1
    assert result.sources[0]["url"] == "https://example.com/scrape"


def test_provider_timeout_is_transport_configuration_not_api_payload() -> None:
    transport = StubTransport()
    provider = make_provider(
        "firecrawl",
        credential_resolver=CredentialResolver(environ={"SEW_FIRECRAWL_API_KEY": "secret"}),
        live_enabled=True,
        transport=transport,
    )

    provider.crawl(
        "https://example.com/root",
        run_id="run-timeout-override",
        timeout_seconds=60.0,
        limit=10,
    )

    assert transport.calls[0]["timeout_seconds"] == 60.0
    assert transport.calls[0]["json_body"] == {
        "limit": 10,
        "url": "https://example.com/root",
    }


def test_stable_call_id_distinguishes_request_options() -> None:
    started_at = "2026-01-01T00:00:00Z"
    keyword = ProviderRequest(
        operation="search",
        run_id="run-options",
        query="same query",
        options={"type": "keyword", "limit": 10},
    )
    neural = ProviderRequest(
        operation="search",
        run_id="run-options",
        query="same query",
        options={"type": "neural", "limit": 10},
    )

    assert stable_call_id("exa", keyword, started_at) != stable_call_id("exa", neural, started_at)


def test_utc_now_preserves_fractional_seconds() -> None:
    assert "." in utc_now()


def test_fixture_missing_operation_is_not_applicable() -> None:
    provider = make_provider("fixture", fixture_run_dir=FIXTURE_ROOT / "success")

    result = provider.crawl("https://fixture.example", run_id="fixture-run")

    assert result.status == "not_applicable"
    assert result.error_class == "fixture_missing_operation"
    assert result.schema_provider_call()["status"] == "not_applicable"


def test_env_ref_credential_resolution_uses_operator_provided_environment() -> None:
    resolver = CredentialResolver(
        environ={
            "SEW_EXA_API_KEY_REF": "env:EXPLICIT_EXA_SECRET",
            "EXPLICIT_EXA_SECRET": "secret-from-env-ref",
        }
    )

    assert resolver.resolve("exa") == ("secret-from-env-ref", "SEW_EXA_API_KEY_REF")


def test_health_reports_unavailable_for_missing_secret_when_live_enabled() -> None:
    provider = make_provider(
        "parallel-web",
        credential_resolver=CredentialResolver(environ={}),
        live_enabled=True,
        transport=StubTransport(),
    )

    health = provider.health()

    assert health.status == "unavailable"
    assert "SEW_PARALLEL_WEB_API_KEY" in (health.detail or "")
