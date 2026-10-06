import hashlib
import io
import json
from pathlib import Path
from urllib.request import HTTPRedirectHandler

import pytest

from sew.cli import main
from sew.gap.mine import mine, _excerpt, _notes, read_url
from sew.schema import SchemaError

FIXTURES = Path(__file__).parent / "fixtures/gap/miner"


def reader(registry=None, release=True):
    def read(url):
        if url == "https://pypi.org/pypi/mini/json":
            return json.dumps(registry) if registry else (FIXTURES / "pypi.json").read_text()
        if url.endswith("/releases/tags/v2.0.0") and release:
            return (FIXTURES / "release.json").read_text()
        if url == "https://raw.githubusercontent.com/example-org/mini/main/CHANGELOG.md":
            return (FIXTURES / "CHANGELOG.md").read_text()
        return None

    return read


def test_recorded_release_ranking_and_receipts(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("network forbidden"))
    result = mine(["mini"], since="2026-01-01", read=reader(), retrieved_at="2026-09-30")
    assert [c["new_version"] for c in result["candidates"]] == ["2.0.0", "1.1.0"]
    first, second = result["candidates"]
    assert first["old_version"] == "1.1.0"
    assert second["old_version"] == "1.0.1"
    assert first["release_kind"] == "major" and second["release_kind"] == "minor"
    assert set(first["markers"]) == {"breaking", "removed", "renamed", "default-change", "security"}
    for candidate in result["candidates"]:
        assert {p["role"] for p in candidate["packages"]} == {"old", "new"}
        assert all(
            p["sha256"] == "a" * 64 and p["url"].endswith(".whl") for p in candidate["packages"]
        )
        oracle = candidate["oracle"]
        assert oracle["retrieved_at"] == "2026-09-30"
        assert oracle["sha256"] == hashlib.sha256(oracle["excerpt"].encode()).hexdigest()
        assert len(oracle["excerpt"].split()) <= 400
        assert oracle["source_url"] in candidate["provenance"]["source_urls"]
    assert mine(["mini"], since="2026-04-01", read=reader())["candidates"] == []


def test_changelog_only_is_release_specific():
    result = mine(["mini"], since="2026-01-01", read=reader(release=False))
    assert [c["new_version"] for c in result["candidates"]] == ["2.0.0", "1.1.0"]
    assert result["candidates"][0]["oracle"]["excerpt"].startswith("## 2.0.0")
    assert "1.1.0" not in result["candidates"][0]["oracle"]["excerpt"]
    assert "3.0.0" in str(result["skipped"])


@pytest.mark.parametrize("damage", ["missing", "hash", "yanked"])
def test_missing_or_invalid_wheels_refused(damage):
    registry = json.loads((FIXTURES / "pypi.json").read_text())
    wheel = registry["releases"]["2.0.0"][0]
    if damage == "missing":
        wheel["packagetype"] = "sdist"
    elif damage == "hash":
        wheel["digests"]["sha256"] = "not-a-hash"
    else:
        wheel["yanked"] = True
    result = mine(["mini"], since="2026-01-01", read=reader(registry))
    assert all(c["new_version"] != "2.0.0" for c in result["candidates"])


def test_long_oracle_is_verbatim_and_keeps_marker():
    body = "intro " * 900 + "Removed the legacy API. " + "detail " * 900
    excerpt = _excerpt(body)
    assert excerpt in body and "Removed" in excerpt and len(excerpt.split()) == 400
    assert _excerpt("Ordinary maintenance.") is None


def test_mine_cli_writes_state(tmp_path, monkeypatch, capsys):
    import sew.gap.mine as miner

    original = miner.mine
    monkeypatch.setattr(
        miner, "mine", lambda packages, **kw: original(packages, read=reader(), **kw)
    )
    assert (
        main(
            [
                "gap",
                "mine",
                "--ecosystem",
                "pypi",
                "--since",
                "2026-01-01",
                "--package",
                "mini",
                "--state-root",
                str(tmp_path),
            ]
        )
        == 0
    )
    record = json.loads((tmp_path / "gap/candidates/2026-01-01.json").read_text())
    assert len(record["candidates"]) == 2
    assert "path" in capsys.readouterr().out


def test_metadata_reader_is_bounded_and_no_credentials(monkeypatch):
    with pytest.raises(SchemaError):
        read_url("https://user:secret@example.org/x")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            assert size == 4_000_001
            return b"x" * size

    monkeypatch.setattr("sew.gap.mine.urlopen", lambda *args, **kw: Response())
    with pytest.raises(SchemaError, match="cap"):
        read_url("https://example.org/x")


@pytest.mark.parametrize("phase", ["open", "read"])
def test_metadata_reader_wraps_timeouts(monkeypatch, phase):
    class Response(io.BytesIO):
        def read(self, size):
            raise TimeoutError("body stalled")

    def open_source(request, *, timeout):
        assert timeout == 30
        if phase == "open":
            raise TimeoutError("connection stalled")
        return Response()

    monkeypatch.setattr("sew.gap.mine.urlopen", open_source)
    with pytest.raises(SchemaError, match="cannot read source") as error:
        read_url("https://example.org/x")
    assert isinstance(error.value.__cause__, TimeoutError)


@pytest.mark.parametrize(
    ("gh_token", "github_token", "expected"),
    [
        (None, None, None),
        ("oauth-gh", None, "oauth-gh"),
        (None, "oauth-github", "oauth-github"),
        ("oauth-gh", "oauth-github", "oauth-gh"),
        ("", "oauth-github", "oauth-github"),
    ],
)
@pytest.mark.parametrize("authority", ["api.github.com", "api.github.com:443"])
def test_metadata_reader_github_oauth_tokens(
    monkeypatch, gh_token, github_token, expected, authority
):
    for key, value in [("GH_TOKEN", gh_token), ("GITHUB_TOKEN", github_token)]:
        monkeypatch.delenv(key, raising=False)
        if value is not None:
            monkeypatch.setenv(key, value)

    def open_source(request, *, timeout):
        assert request.get_header("Authorization") == (f"Bearer {expected}" if expected else None)
        assert request.get_header("User-agent") == "agent-os-sew-gap/1"
        assert timeout == 30
        redirected = HTTPRedirectHandler().redirect_request(
            request, None, 302, "Found", {}, "https://example.org/notes"
        )
        assert redirected.get_header("Authorization") is None
        return io.BytesIO(b"{}")

    monkeypatch.setattr("sew.gap.mine.urlopen", open_source)
    assert read_url(f"https://{authority}/repos/example-org/mini/releases") == "{}"


@pytest.mark.parametrize(
    "url",
    [
        "https://pypi.org/pypi/mini/json",
        "https://raw.githubusercontent.com/example-org/mini/notes",
        "https://api.github.com.example.org/notes",
        "https://api.github.com:8443/notes",
    ],
)
def test_metadata_reader_keeps_oauth_tokens_off_other_origins(monkeypatch, url):
    monkeypatch.setenv("GH_TOKEN", "oauth-gh")
    monkeypatch.setenv("GITHUB_TOKEN", "oauth-github")

    def open_source(request, *, timeout):
        assert request.get_header("Authorization") is None
        return io.BytesIO(b"notes")

    monkeypatch.setattr("sew.gap.mine.urlopen", open_source)
    assert read_url(url) == "notes"


@pytest.mark.parametrize("tag", ["v2.0.0", "2.0.0"])
def test_release_tag_lookup_stops_at_first_usable_release(tag):
    requests = []
    prefix = "https://api.github.com/repos/example-org/mini/releases/tags/"

    def read(url):
        requests.append(url)
        if url == prefix + tag:
            return (FIXTURES / "release.json").read_text()
        return None

    notes = list(
        _notes({"project_urls": {"Source": "https://github.com/example-org/mini"}}, "2.0.0", read)
    )
    assert len(notes) == 1
    assert [url for url in requests if url.startswith(prefix)] == [
        prefix + candidate
        for candidate in (["v2.0.0"] if tag.startswith("v") else ["v2.0.0", "2.0.0"])
    ]
