from __future__ import annotations

import hashlib
import json
import shutil
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import MODULE_ROOT
from test_bakeoff_report import FACT, FRESH, GENERATED_AT, _arm, _run_root, _state, _usage

import sew.bakeoff_bundle as bakeoff_bundle
from sew.bakeoff_bundle import (
    MANIFEST_NAME,
    PLACEHOLDER_RE,
    PUBLISHED_REPORT_NAME,
    REDACTION_REPORT_NAME,
    BundleError,
    aggregate_view,
    assemble_bundle,
    explain_task,
    resolve_run,
    sweep_bundle,
    verify_bundle,
)
from sew.bakeoff_report import build_bakeoff_report
from sew.cli import main as cli_main
from sew.cost_model import load_price_table
from sew.harness import assert_no_secret_material
from sew.mcp_meter import AVAILABILITY_REF, availability_record, write_availability
from sew.runner import SuiteRunner

PRICE_TABLE = {
    "version": 1,
    "vendors": {},
    "models": {
        "claude-sonnet-5": {
            "input_usd_per_mtok": 2.0,
            "cached_input_usd_per_mtok": 0.2,
            "output_usd_per_mtok": 10.0,
            "rate_basis": "published_list_price",
            "source": "https://example.test/pricing",
            "as_of": "2026-09-01",
        }
    },
}


def test_ambient_token_flag_is_not_a_credential_value() -> None:
    assert bakeoff_bundle.known_credentials(
        {
            "AGENT_OS_GITHUB_ADAPTER_ENV_TOKEN_FROM_AMBIENT_GH": "1",
            "GH_TOKEN": "actual-token-value",
        }
    ) == ("actual-token-value",)


# Credential material planted in the source run. None of it may reach a bundle.
EXA_KEY = "exa-live-8f3a9c2e7d1b4f60a5c3"  # no known shape: only the host's env names it
ANTHROPIC_KEY = "sk-ant-api03-" + "Q7" * 16
BEARER = "Bearer abcdefghijklmnopqrstuvwxyz0123456789"
COOKIE = "session=deadbeefcafe0123"
SECRETS = (EXA_KEY, ANTHROPIC_KEY, BEARER.split()[1], COOKIE)
ENVIRON = {"SEW_EXA_API_KEY": EXA_KEY, "PATH": "/usr/bin"}
TOOL = "mcp__exa__web_search_exa"


@pytest.mark.parametrize('installed_catalog_present', [True, False])
def test_oss_bundle_uses_catalog_snapshot_for_prices_and_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed_catalog_present: bool,
) -> None:
    from sew import oss

    root = _run_root(tmp_path)
    entries = _arm(
        root, "opencode", "no-search", ["pass"] * 3,
        usage=_usage(1_000_000, 0, 100_000, 0), model_profile="litellm/glm-5.2",
    )
    _state(root, entries)
    prices = tmp_path / "prices.yaml"
    prices.write_text(yaml.safe_dump(PRICE_TABLE), encoding="utf-8")
    artifacts = _assemble(root, tmp_path / "pub", prices)
    published = json.loads(artifacts.report_json.read_text())
    expected_cost = published["runs"][0]["cost_usd"]
    assert expected_cost is not None and expected_cost > 0
    expected_rate = published["headline"][0]["model_rate"]
    snapshot = artifacts.bundle_dir / "manifests/config/oss-models.yaml"
    assert snapshot.read_bytes() == (MODULE_ROOT / "config/oss-models.yaml").read_bytes()

    # Unpack on a host with a different catalog (or no installed catalog).
    moved = tmp_path / "elsewhere" / artifacts.bundle_dir.name
    shutil.move(str(artifacts.bundle_dir), moved)
    installed = tmp_path / "installed"
    if installed_catalog_present:
        catalog = yaml.safe_load(
            (moved / "manifests/config/oss-models.yaml").read_text()
        )
        route = catalog['models']['glm-5.2']
        route.update(input_usd_per_mtok=99, output_usd_per_mtok=99,
                     source="https://example.test/new-pricing", as_of="2026-10-10")
        (installed / "config").mkdir(parents=True)
        (installed / "config/oss-models.yaml").write_text(yaml.safe_dump(catalog))
    monkeypatch.setattr(oss, "module_root", lambda: installed)
    result = verify_bundle(moved, environ=ENVIRON)
    assert result['ok'], result
    resolved = resolve_run(str(moved))
    derived = build_bakeoff_report(
        resolved.run_root, module_base=resolved.module_base,
        price_table=load_price_table(resolved.price_table_path),
    )
    assert aggregate_view(derived) == aggregate_view(published)
    assert derived['headline'][0]['model_rate'] == expected_rate
    assert derived['runs'][0]['cost_usd'] == expected_cost
    explained = explain_task(resolved, FACT)
    assert explained['arms'][0]['successes'] == explained['arms'][0]['attempted'] == 3


def test_bundle_round_trips_through_the_report_generator_to_identical_aggregates(
    tmp_path: Path,
) -> None:
    root, prices = _mixed_run(tmp_path)
    artifacts = _assemble(root, tmp_path / "pub", prices)

    source = build_bakeoff_report(
        root,
        module_base=MODULE_ROOT,
        generated_at=GENERATED_AT,
        price_table=load_price_table(prices),
    )
    published = json.loads(artifacts.report_json.read_text(encoding="utf-8"))
    assert aggregate_view(published) == aggregate_view(source)
    for key in ("headline", "by_task_class", "deltas", "marked_cells", "runs"):
        assert published[key], key
    # The billed model is re-read from a transcript whose text was withheld:
    # protocol fields survive redaction, so the priced cost does too.
    control = next(run for run in published["runs"] if run["arm"] == "claude-code+no-search")
    assert control["model_id_source"] == "harness_transcript"
    assert control["cost_usd"] == pytest.approx((1000 * 2.0 + 500 * 0.2 + 300 * 10.0) / 1e6)
    assert "## Evidence index" in artifacts.report_markdown.read_text(encoding="utf-8")

    # The bundle is self-contained: moved anywhere, it re-derives the same numbers.
    moved = tmp_path / "elsewhere" / artifacts.bundle_dir.name
    shutil.move(str(artifacts.bundle_dir), moved)
    result = verify_bundle(moved, environ=ENVIRON)
    assert result["ok"], result
    assert result["aggregates_identical"]
    manifest = json.loads((moved / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert result["aggregates_sha256"] == manifest["aggregates_sha256"]
    index = json.loads((moved / "runs" / "wsb-fixture" / "run-index.json").read_text())
    assert {entry["run_dir"] for entry in index} == {
        f"bundles/{entry['run_id']}" for entry in index
    }
    contents = manifest["contents"]
    for role in ("task_manifests", "transcripts", "usage_records", "provider_calls"):
        assert contents[role], role
        assert all((moved / path).is_file() for path in contents[role]), role

    assert "manifests/tasks/current-fact-lookup-v1/task.yaml" in contents["task_manifests"]
    assert len(contents["judge_records"]) == len(index)
    arms = json.loads((moved / "manifests" / "arms.json").read_text())["arms"]
    assert {arm["arm"] for arm in arms} >= {"claude-code+exa", "claude-code+no-search"}

    # A reader who edits a record gets a failed check, not a quietly different number.
    metrics = moved / "runs" / "wsb-fixture" / "bundles" / _run_id("exa", 1) / "metrics"
    usage = json.loads((metrics / "metrics.json").read_text())
    usage["token_usage"] = _usage(9000, 0, 100, 0)
    (metrics / "metrics.json").write_text(json.dumps(usage))
    tampered = verify_bundle(moved, environ=ENVIRON)
    assert not tampered["ok"]
    assert tampered["files"]["modified"] == [
        f"runs/wsb-fixture/bundles/{_run_id('exa', 1)}/metrics/metrics.json"
    ]
    assert not tampered["aggregates_identical"]
    assert any("/tokens/" in pointer for pointer in tampered["differences"])


def test_availability_evidence_is_published_separately_from_provider_calls(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    run_id = _run_id("exa", 1)
    run_dir = root / "bundles" / run_id
    availability = availability_record("exa", run_id)
    availability.update(
        initialize_requested=True,
        initialized=True,
        tools_list_requested=True,
        tools_list_completed=True,
        tool_count=1,
    )
    write_availability(run_dir / "provider-calls", availability)

    artifacts = _assemble(root, tmp_path / "pub", prices)
    manifest = json.loads((artifacts.bundle_dir / MANIFEST_NAME).read_text())
    published_ref = f"runs/wsb-fixture/bundles/{run_id}/{AVAILABILITY_REF}"
    calls = manifest["contents"]["provider_calls"]
    assert published_ref not in calls
    assert calls  # The actual call records are still listed.
    assert manifest["contents"]["provider_availability"] == [published_ref]
    assert json.loads((artifacts.bundle_dir / published_ref).read_text()) == availability
    assert published_ref in {entry["path"] for entry in manifest["files"]}
    assert verify_bundle(artifacts.bundle_dir, environ=ENVIRON)["ok"]


def test_codex_native_pricing_survives_redacted_bundle_round_trip(tmp_path: Path) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "codex",
        "native",
        ["pass"],
        usage=_usage(1000, 0, 500, 0),
        model_id="gpt-6-sol",
        search_calls=3,
    )
    run_id = entries[0]["run_id"]
    transcript = []
    for call_id, action in (
        ("ws1", "search"),
        ("ws2", "open_page"),
        ("ws3", "Private arbitrary action"),
    ):
        item = {
            "type": "web_search",
            "id": call_id,
            "action": {
                "type": action,
                "query": "Private customer search",
                "url": "https://private.example.test/customer",
            },
            "result": {"type": "private result", "id": "private-id"},
        }
        transcript.extend(
            [
                {"harness_event": {"type": "item.started", "item": item}},
                {"harness_event": {"type": "item.completed", "item": item}},
            ]
        )
    (Path(entries[0]["run_dir"]) / "artifacts" / "transcript.json").write_text(
        json.dumps(transcript)
    )
    _state(root, entries)
    prices = MODULE_ROOT / "config" / "price-table.yaml"
    source = build_bakeoff_report(
        root,
        module_base=MODULE_ROOT,
        generated_at=GENERATED_AT,
        price_table=load_price_table(prices),
    )
    artifacts = _assemble(root, tmp_path / "pub", prices)
    result = verify_bundle(artifacts.bundle_dir, environ=ENVIRON)
    assert result["ok"] and result["aggregates_identical"]
    published = json.loads(artifacts.report_json.read_text())
    assert aggregate_view(published) == aggregate_view(source)
    assert published["runs"][0]["cost_usd"] == pytest.approx(0.017)

    transcript_path = (
        artifacts.bundle_dir
        / "runs"
        / root.name
        / "bundles"
        / run_id
        / "artifacts"
        / "transcript.json"
    )
    redacted = json.loads(transcript_path.read_text())
    for index, event in enumerate(redacted):
        item = event["harness_event"]["item"]
        assert item["type"] == "web_search"
        assert item["id"] == f"ws{index // 2 + 1}"
        assert item["action"]["type"] in {"search", "open_page", "<redacted:raw-transcript>"}
        assert item["action"]["query"] == "<redacted:raw-transcript>"
        assert item["action"]["url"] == "<redacted:raw-transcript>"
        assert item["result"]["type"] == "<redacted:raw-transcript>"
        assert item["result"]["id"] == "<redacted:raw-transcript>"
    assert "Private arbitrary action" not in transcript_path.read_text()
    assert "Private customer" not in transcript_path.read_text()
    assert "private.example.test" not in transcript_path.read_text()


def test_older_report_version_is_identified_without_false_aggregate_drift(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    report_path = bundle / manifest["layout"]["report_json"]
    report = json.loads(report_path.read_text())
    report["schema_version"] = 1
    report_path.write_text(json.dumps(report))
    for item in manifest["files"]:
        if item["path"] == manifest["layout"]["report_json"]:
            item["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
            item["size_bytes"] = report_path.stat().st_size
    manifest_path.write_text(json.dumps(manifest))

    result = verify_bundle(bundle, environ=ENVIRON)
    assert not result["ok"]
    assert result["report_version_status"] == "unsupported_report_version"
    assert "mismatch" in result["report_version_note"]
    manifest["report_schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest))
    result = verify_bundle(bundle, environ=ENVIRON)
    assert result["report_version_status"] == "older_report_version"
    assert result["files"]["modified"] == []
    assert result["aggregates_identical"] is None
    assert result["differences"] == result["rendered_drift"] == []
    # Genuine pre-version manifests remain archivable, with an explicit caveat.
    del manifest["report_schema_version"]
    manifest_path.write_text(json.dumps(manifest))
    legacy = verify_bundle(bundle, environ=ENVIRON)
    assert legacy["report_version_status"] == "legacy_report_version_unverified"
    assert "not independently authenticated" in legacy["report_version_note"]
    archive = bundle.parent / f"{root.name}.tar.gz"
    archive.unlink()
    _assemble(root, bundle.parent, prices)
    assert archive.is_file()
    archive.unlink()
    report_path.write_text(json.dumps({**report, "headline": []}))
    with pytest.raises(BundleError, match="unverified"):
        _assemble(root, bundle.parent, prices)


def test_a_fixture_suite_run_bundles_and_verifies_through_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = SuiteRunner(state_root=tmp_path).run("lighthouse", run_id="fx", resume=False)
    run_root = Path(summary["run_root"])
    capsys.readouterr()

    assert cli_main(["bakeoff", "bundle", str(run_root)]) == 0
    written = json.loads(capsys.readouterr().out)
    bundle = Path(written["bundle_dir"])
    assert bundle == run_root / "publication" / "fx"
    assert written["raw_transcripts_included"] is False
    assert (bundle / PUBLISHED_REPORT_NAME).is_file()

    assert cli_main(["bakeoff", "verify", str(bundle)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    # The runner's retries are evidence too: every attempt directory is bundled.
    index = json.loads((bundle / "runs" / "fx" / "run-index.json").read_text())
    for entry in index:
        for attempt in entry.get("attempt_run_dirs", []):
            assert attempt.startswith("bundles/")
            assert (bundle / "runs" / "fx" / attempt / "run.json").is_file()

    # The shareable archive unpacks to the same verified bundle.
    unpacked = tmp_path / "unpacked"
    with tarfile.open(written["archive"]) as tar:
        tar.extractall(unpacked, filter="data")
    assert verify_bundle(unpacked / "fx", environ={})["ok"]

    # A publication is immutable.
    assert cli_main(["bakeoff", "bundle", str(run_root)]) == 2
    assert "refusing to overwrite" in capsys.readouterr().err


@pytest.mark.parametrize("field", ["run_dir", "attempt_run_dirs"])
def test_indexed_run_directories_must_stay_under_suite_bundles(tmp_path: Path, field: str) -> None:
    root, prices = _mixed_run(tmp_path)
    private = tmp_path / "private"
    private.mkdir()
    (private / "customer.txt").write_text("Private customer")
    index_path = root / "run-index.json"
    index = json.loads(index_path.read_text())
    if field == "run_dir":
        index[0][field] = str(private)
    else:
        index[0][field] = [str(private)]
    index_path.write_text(json.dumps(index))

    with pytest.raises(BundleError, match="escapes suite bundles"):
        _assemble(root, tmp_path / "pub", prices)
    assert not (tmp_path / "pub").exists()

    # A path that looks local but resolves through a symlink is external too.
    link = root / "bundles" / "private-link"
    link.symlink_to(private, target_is_directory=True)
    index[0][field] = "bundles/private-link" if field == "run_dir" else ["bundles/private-link"]
    index_path.write_text(json.dumps(index))
    with pytest.raises(BundleError, match="escapes suite bundles"):
        _assemble(root, tmp_path / "pub", prices)


def test_deferred_unavailable_bundles_ship_with_relative_paths(tmp_path: Path) -> None:
    """A streak-deferred cell's walled bundle is evidence; its host path must not publish."""

    root, prices = _mixed_run(tmp_path)
    walled = root / "bundles" / f"{_run_id('exa', 1)}-walled"
    shutil.copytree(root / "bundles" / _run_id("exa", 1), walled)
    state_path = root / "runner-state.json"
    state = json.loads(state_path.read_text())
    state["deferred_unavailable"] = {
        "cell-walled": {"deferrals": 1, "attempt_run_dirs": [str(walled)]}
    }
    state_path.write_text(json.dumps(state))

    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir
    run_root = bundle / "runs" / "wsb-fixture"
    shipped = json.loads((run_root / "runner-state.json").read_text())
    attempts = shipped["deferred_unavailable"]["cell-walled"]["attempt_run_dirs"]
    assert attempts == [f"bundles/{walled.name}"]
    assert (run_root / attempts[0] / "run.json").is_file()
    assert str(tmp_path) not in (run_root / "runner-state.json").read_text()

    private = tmp_path / "private"
    private.mkdir()
    state["deferred_unavailable"]["cell-walled"]["attempt_run_dirs"] = [str(private)]
    state_path.write_text(json.dumps(state))
    with pytest.raises(BundleError, match="escapes suite bundles"):
        _assemble(root, tmp_path / "pub2", prices)


@pytest.mark.parametrize("field", ["run_dir", "attempt_run_dirs"])
@pytest.mark.parametrize("path_kind", ["absolute", "parent", "symlink"])
def test_verify_and_explain_reject_uncontained_bundle_runs(
    tmp_path: Path, field: str, path_kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, prices = _mixed_run(tmp_path)
    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir
    private = tmp_path / "private"
    private.mkdir()
    (private / "customer.txt").write_text("Private customer")
    run_root = bundle / "runs" / "wsb-fixture"
    if path_kind == "symlink":
        (run_root / "bundles" / "private-link").symlink_to(private, target_is_directory=True)
        value = "bundles/private-link"
    elif path_kind == "parent":
        value = "../../../private"
    else:
        value = str(private)
    index_path = run_root / "run-index.json"
    index = json.loads(index_path.read_text())
    index[0][field] = value if field == "run_dir" else [value]
    index_path.write_text(json.dumps(index))
    with monkeypatch.context() as patch:
        patch.setattr(
            bakeoff_bundle,
            "build_bakeoff_report",
            lambda *_args, **_kwargs: pytest.fail("report read before path validation"),
        )
        error = "bundle contains symlink" if path_kind == "symlink" else "suite bundles"
        with pytest.raises(BundleError, match=error):
            verify_bundle(bundle, environ=ENVIRON)
        with pytest.raises(BundleError, match=error):
            explain_task(resolve_run(str(bundle)), FACT)


@pytest.mark.parametrize("published_path", ["README.md", "report_markdown"])
def test_verify_rejects_rewritten_headline_even_with_updated_inventory(
    tmp_path: Path, published_path: str
) -> None:
    root, prices = _mixed_run(tmp_path)
    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir
    manifest_path = bundle / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text())
    rel = manifest["layout"].get(published_path, published_path)
    output = bundle / rel
    output.write_text("# All arms passed\n" + output.read_text())
    for item in manifest["files"]:
        if item["path"] == rel:
            item["sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
            item["size_bytes"] = output.stat().st_size
    manifest_path.write_text(json.dumps(manifest))
    result = verify_bundle(bundle, environ=ENVIRON)
    assert not result["ok"]
    assert result["files"]["modified"] == []
    assert result["aggregates_identical"]
    assert result["rendered_drift"] == [rel]


def test_failed_archive_is_removed_and_verified_publication_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, prices = _mixed_run(tmp_path)
    destination = tmp_path / "pub"
    with monkeypatch.context() as patch:
        patch.setattr(
            bakeoff_bundle.tarfile, "open", lambda **_kwargs: (_ for _ in ()).throw(OSError("full"))
        )
        with pytest.raises(OSError, match="full"):
            _assemble(root, destination, prices)

    bundle = destination / "wsb-fixture"
    archive = destination / "wsb-fixture.tar.gz"
    assert bundle.is_dir()
    assert verify_bundle(bundle, environ=ENVIRON)["ok"]
    assert not archive.exists()
    assert not list(destination.glob("*.partial"))
    assert not list(destination.glob(".*.partial"))
    manifest_before = (bundle / MANIFEST_NAME).read_bytes()

    resumed = _assemble(root, destination, prices)
    assert resumed.bundle_dir == bundle
    assert resumed.archive_path == archive
    assert archive.is_file()
    assert (bundle / MANIFEST_NAME).read_bytes() == manifest_before
    with tarfile.open(archive) as tar:
        assert tar.getmembers()


def test_concurrent_archive_writers_stage_in_distinct_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fixed `<archive>.partial` staging name let two concurrent bundle
    # processes interleave writes into one file and rename a corrupt archive.
    root, prices = _mixed_run(tmp_path)
    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir
    target = tmp_path / "out" / "wsb-fixture.tar.gz"
    target.parent.mkdir()
    staged: list[str] = []
    real_mkstemp = bakeoff_bundle.tempfile.mkstemp

    def spy(*args: Any, **kwargs: Any) -> tuple[int, str]:
        fd, name = real_mkstemp(*args, **kwargs)
        staged.append(name)
        return fd, name

    monkeypatch.setattr(bakeoff_bundle.tempfile, "mkstemp", spy)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: bakeoff_bundle.write_archive(bundle, target), range(2)))

    assert len(set(staged)) == 2
    assert {Path(name).parent for name in staged} == {target.parent}
    assert not list(target.parent.glob("*.partial"))
    assert not list(target.parent.glob(".*.partial"))
    unpacked = tmp_path / "unpacked"
    with tarfile.open(target) as tar:
        tar.extractall(unpacked, filter="data")
    assert verify_bundle(unpacked / bundle.name, environ=ENVIRON)["ok"]


def test_runs_sharing_a_directory_name_bundle_under_distinct_names(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    index_path = root / "run-index.json"
    state_path = root / "runner-state.json"
    index = json.loads(index_path.read_text())
    state = json.loads(state_path.read_text())
    moved: dict[str, str] = {}
    for position, group in ((0, "A"), (1, "B")):
        old = Path(index[position]["run_dir"])
        new = root / "bundles" / group / "run1"
        new.parent.mkdir(parents=True)
        old.rename(new)
        moved[str(old)] = str(new)

    def remap(entries: list[dict[str, Any]]) -> None:
        for entry in entries:
            entry["run_dir"] = moved.get(entry.get("run_dir"), entry.get("run_dir"))
            if isinstance(entry.get("attempt_run_dirs"), list):
                entry["attempt_run_dirs"] = [moved.get(v, v) for v in entry["attempt_run_dirs"]]

    remap(index)
    remap(state["index"])
    index_path.write_text(json.dumps(index))
    state_path.write_text(json.dumps(state))

    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir

    bundled = json.loads((bundle / "runs" / "wsb-fixture" / "run-index.json").read_text())
    names = {bundled[0]["run_dir"], bundled[1]["run_dir"]}
    assert names == {"bundles/run1", "bundles/B__run1"}
    for name in names:
        assert (bundle / "runs" / "wsb-fixture" / name).is_dir()
    assert verify_bundle(bundle, environ=ENVIRON)["ok"]


def test_the_redaction_report_lists_every_stripped_field(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    artifacts = _assemble(root, tmp_path / "pub", prices)
    bundle = artifacts.bundle_dir
    report = json.loads(artifacts.redaction_report.read_text(encoding="utf-8"))
    listed = {
        (entry["path"], item["pointer"]): item["rule"]
        for entry in report["files"]
        for item in entry["fields"]
    }

    # Every placeholder in the bundle is accounted for, and every listed field
    # that leaves a placeholder has one where the report says it is.
    placeholders = _placeholder_locations(bundle)
    assert placeholders
    assert placeholders <= set(listed)
    for (path, pointer), rule in listed.items():
        if rule == "host_path":
            assert _at(bundle / path, pointer).startswith("bundles/")
        else:
            assert (path, pointer) in placeholders, (path, pointer, rule)
    assert report["summary"]["fields"] == sum(len(entry["fields"]) for entry in report["files"])

    run = f"runs/wsb-fixture/bundles/{_run_id('exa', 1)}"
    transcript = f"{run}/artifacts/transcript.json"
    for pointer, rule in (
        ("/1/content", "raw_transcript"),  # the prompt
        ("/3/harness_event/message/content/0/text", "raw_transcript"),
        ("/3/harness_event/message/content/1/input/query", "raw_transcript"),
        # Inside a tool's arguments even a key named like a protocol field is data.
        ("/3/harness_event/message/content/1/input/name", "raw_transcript"),
        ("/4/harness_event/message/content/0/content/name", "raw_transcript"),
        ("/4/harness_event/message/content/0/content/items/0/name", "raw_transcript"),
        ("/5/content", "raw_transcript"),
    ):
        assert listed[(transcript, pointer)] == rule, pointer
    assert listed[(f"{run}/artifacts/harness-stderr.txt", "")] == "raw_transcript"
    spawn = f"{run}/artifacts/spawn-metadata.json"
    assert listed[(spawn, "/mcp_headers/x-api-key")] == "credential_key"
    assert listed[(spawn, "/env/SEW_EXA_API_KEY")] == "capture"
    assert listed[("runs/wsb-fixture/run-index.json", "/0/run_dir")] == "host_path"
    source_bytes = (
        root / "bundles" / _run_id("exa", 1) / "artifacts" / "transcript.json"
    ).read_bytes()
    entry = next(item for item in report["files"] if item["path"] == transcript)
    assert entry["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()

    # What a reader needs to re-check the arm and the bill is still there.
    events = json.loads((bundle / transcript).read_text())
    assert events[2]["harness_event"]["model"] == "claude-sonnet-5"
    assert events[2]["harness_event"]["tools"] == ["Read", TOOL]
    assert events[3]["harness_event"]["message"]["content"][1]["name"] == TOOL
    result = events[4]["harness_event"]["message"]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "toolu_1"
    assert result["content"]["name"] == "<redacted:raw-transcript>"
    assert "Private customer" not in (bundle / transcript).read_text()
    assert events[6]["usage"]["input_tokens"] == 1000
    assert events[3]["harness_event"]["data"]["name"] == "<redacted:raw-transcript>"
    assert events[3]["harness_event"]["data"]["client_id"] == "<redacted:raw-transcript>"

    # Opting in keeps transcript text, and the report says so.
    raw = _assemble(root, tmp_path / "raw", prices, include_raw_transcripts=True)
    raw_report = json.loads(raw.redaction_report.read_text())
    assert raw_report["raw_transcripts_included"] is True
    assert "raw_transcript" not in {item["rule"] for item in raw_report["summary"]["by_rule"]}
    raw_events = json.loads((raw.bundle_dir / transcript).read_text())
    assert raw_events[5]["content"] == "Found it: https://docs.example.test/changelog"
    manifest = json.loads((raw.bundle_dir / MANIFEST_NAME).read_text())
    assert manifest["raw_transcripts_included"] is True
    assert "**included by operator opt-in**" in raw.published_report.read_text()


@pytest.mark.parametrize("include_raw_transcripts", [False, True])
def test_no_credential_material_appears_anywhere_in_a_bundle(
    tmp_path: Path, include_raw_transcripts: bool
) -> None:
    root, prices = _mixed_run(tmp_path)
    artifacts = _assemble(
        root, tmp_path / "pub", prices, include_raw_transcripts=include_raw_transcripts
    )
    bundle = artifacts.bundle_dir
    source_texts = "\n".join(path.read_text() for path in root.rglob("*") if path.is_file())
    assert all(secret in source_texts for secret in SECRETS)

    unpacked = tmp_path / "unpacked"
    with tarfile.open(artifacts.archive_path) as tar:
        tar.extractall(unpacked, filter="data")
    for tree in (bundle, unpacked):
        files = [path for path in tree.rglob("*") if path.is_file()]
        assert files
        for path in files:
            text = path.read_text(encoding="utf-8")
            leaked = [secret for secret in SECRETS if secret in text]
            assert not leaked, (path, leaked)
    assert_no_secret_material(bundle)
    assert sweep_bundle(bundle, [EXA_KEY]) == []
    manifest = json.loads((bundle / MANIFEST_NAME).read_text())
    assert manifest["credentials_included"] is False
    assert verify_bundle(bundle, environ=ENVIRON)["credential_findings"] == []


def test_credentials_in_json_keys_and_yaml_comments_are_scrubbed(tmp_path: Path) -> None:
    # The value walk never visits JSON keys or YAML comments; the serialized
    # text must be swept too, or the bundle publishes a credential there.
    root, prices = _mixed_run(tmp_path)
    run_dir = root / "bundles" / _run_id("exa", 1)
    (run_dir / "notes.json").write_text(json.dumps({ANTHROPIC_KEY: "seen"}))
    (run_dir / "notes.yaml").write_text(f"# key {ANTHROPIC_KEY}\nstatus: ok\n")

    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir

    published = [path for path in bundle.rglob("notes.*") if path.is_file()]
    assert {path.name for path in published} == {"notes.json", "notes.yaml"}
    for path in published:
        text = path.read_text(encoding="utf-8")
        assert ANTHROPIC_KEY not in text, path
        assert "<redacted:" in text
    assert json.loads(next(p for p in published if p.suffix == ".json").read_text())
    assert yaml.safe_load(next(p for p in published if p.suffix == ".yaml").read_text())
    assert sweep_bundle(bundle, [EXA_KEY]) == []
    assert verify_bundle(bundle, environ=ENVIRON)["ok"]


def test_a_symlinked_suite_bundles_directory_is_accepted(tmp_path: Path) -> None:
    root, prices = _mixed_run(tmp_path)
    elsewhere = tmp_path / "other-disk" / "bundles"
    elsewhere.parent.mkdir()
    (root / "bundles").rename(elsewhere)
    (root / "bundles").symlink_to(elsewhere, target_is_directory=True)

    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir

    assert verify_bundle(bundle, environ=ENVIRON)["ok"]


@pytest.mark.parametrize("include_raw_transcripts", [False, True])
def test_short_host_password_is_removed_from_published_text(
    tmp_path: Path, include_raw_transcripts: bool
) -> None:
    root, prices = _mixed_run(tmp_path)
    secret = "xQ7!"
    prompt = root / "bundles" / _run_id("exa", 1) / "artifacts" / "prompt.md"
    prompt.write_text(f"Task prompt with temporary password: {secret}\n")
    env = {**ENVIRON, "SHORT_PASSWORD": secret}
    artifacts = assemble_bundle(
        root,
        output_dir=tmp_path / "pub",
        module_base=MODULE_ROOT,
        price_table_path=prices,
        generated_at=GENERATED_AT,
        environ=env,
        include_raw_transcripts=include_raw_transcripts,
    )
    assert (
        secret
        not in (
            artifacts.bundle_dir
            / "runs"
            / "wsb-fixture"
            / "bundles"
            / _run_id("exa", 1)
            / "artifacts"
            / "prompt.md"
        ).read_text()
    )
    assert verify_bundle(artifacts.bundle_dir, environ=env)["ok"]


def test_a_bundle_that_fails_its_checks_is_never_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, prices = _mixed_run(tmp_path)
    destination = tmp_path / "pub"

    # A scrubber that misses credential shapes: the sweep refuses the bundle.
    with monkeypatch.context() as patch:
        patch.setattr(bakeoff_bundle, "scrub_credentials", lambda text: text)
        with pytest.raises(BundleError, match="credential sweep refused the bundle"):
            _assemble(root, destination, prices, include_raw_transcripts=True)
    assert list(destination.iterdir()) == []

    # Redaction that strips a field the report reads: the drift check refuses it.
    with monkeypatch.context() as patch:
        patch.setattr(
            bakeoff_bundle, "TRANSCRIPT_KEPT_KEYS", bakeoff_bundle.TRANSCRIPT_KEPT_KEYS - {"model"}
        )
        with pytest.raises(BundleError, match="drifted from the source run's report"):
            _assemble(root, destination, prices)
    assert list(destination.iterdir()) == []


def test_explain_renders_per_task(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _run_root(tmp_path)
    entries = _arm(
        root, "claude-code", "exa", ["pass", "pass", "fail"], usage=_usage(900, 0, 100, 0)
    )
    entries += _arm(root, "claude-code", "firecrawl", ["fail", "timeout", "pass"])
    entries += _arm(root, "claude-code", "native", ["fail", "fail"], usage=_usage(400, 0, 100, 0))
    entries += _arm(root, "claude-code", "native", ["pass"], first_rep=3, contaminated=True)
    entries += _arm(root, "claude-code", "exa", ["fail"] * 3, task_id=FRESH, first_rep=11)
    _state(root, entries)
    # Graded failures score correct=false unless told otherwise; this one was
    # right but cited nothing.
    _dimensions(root, "claude-code-exa-current-fact-lookup-v1-3", correct=True, grounded=False)

    assert cli_main(["bakeoff", "explain", str(root), "--task", FACT]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"# Explain: {FACT} (task_class=fact_lookup)")
    rows = _arm_rows(out)
    assert rows["claude-code+exa"] == ["2/3", "grounded (1)", "1,000 (3/3)", "-"]
    assert rows["claude-code+firecrawl"] == [
        "1/3",
        "completed (1), correct (1)",
        "unmeasured (0/3)",
        "timeout (1)",
    ]
    # The contaminated run is not scored, so it is a note and not an attempt.
    assert rows["claude-code+native"] == ["0/2", "correct (2)", "500 (2/2)", "contaminated (1)"]
    assert "claude-code-exa-freshness-stale-trap-v1-11" not in out
    assert "[transcript](bundles/claude-code-exa-current-fact-lookup-v1-3/artifacts/" in out

    # The same view over the published bundle, and by suite run id.
    prices = tmp_path / "prices.yaml"
    prices.write_text(yaml.safe_dump(PRICE_TABLE))
    bundle = _assemble(root, tmp_path / "pub", prices).bundle_dir
    before = {
        path.relative_to(bundle): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    assert cli_main(["bakeoff", "explain", str(bundle), "--task", FACT]) == 0
    assert _arm_rows(capsys.readouterr().out) == rows
    assert {
        path.relative_to(bundle): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in bundle.rglob("*")
        if path.is_file()
    } == before
    assert verify_bundle(bundle, environ=ENVIRON)["ok"]
    assert resolve_run("wsb-fixture", state_root=tmp_path).run_root == root

    assert cli_main(["bakeoff", "explain", str(root), "--task", "no-such-task"]) == 2
    err = capsys.readouterr().err
    assert "task 'no-such-task' has no runs in wsb-fixture" in err
    assert FRESH in err


# --------------------------------------------------------------------------
# A suite run with every kind of evidence, and credentials planted in it
# --------------------------------------------------------------------------


def _mixed_run(tmp_path: Path) -> tuple[Path, Path]:
    root = _run_root(tmp_path)
    entries = _arm(
        root,
        "claude-code",
        "no-search",
        ["pass", "fail", "pass"],
        usage=_usage(1000, 500, 200, 100),
        transcript_model="claude-sonnet-5",
    )
    entries += _arm(
        root,
        "claude-code",
        "exa",
        ["pass", "pass", "fail"],
        usage=_usage(1000, 0, 100, 0),
        model_id="claude-sonnet-5",
        vendor_usd=0.01,
    )
    entries += _arm(root, "claude-code", "exa", ["pass"], first_rep=4, contaminated=True)
    entries += _arm(root, "claude-code", "native", ["pass", "ungraded", "fail"], usage=None)
    entries += _arm(root, "claude-code", "exa", ["pass"] * 3, task_id=FRESH, first_rep=11)
    _state(root, entries)
    _plant_live_evidence(root / "bundles" / _run_id("exa", 1))
    prices = tmp_path / "prices.yaml"
    prices.write_text(yaml.safe_dump(PRICE_TABLE), encoding="utf-8")
    return root, prices


def _plant_live_evidence(run_dir: Path) -> None:
    """A live-shaped transcript, stderr, spawn record and provider call with secrets in them."""

    transcript = [
        {"event": "prompt_sent", "role": "system", "timestamp": "2026-09-26T00:00:00Z"},
        {"role": "user", "content": "List the breaking changes in the 3.0 changelog."},
        {
            "role": "harness",
            "received_at": "2026-09-26T00:00:01Z",
            "harness_event": {
                "type": "system",
                "subtype": "init",
                "model": "claude-sonnet-5",
                "cwd": "/Users/operator/scratch/cell",
                "tools": ["Read", TOOL],
            },
        },
        {
            "role": "harness",
            "received_at": "2026-09-26T00:00:02Z",
            "harness_event": {
                "type": "assistant",
                "data": {"name": "Confidential client", "client_id": 12345},
                "message": {
                    "model": "claude-sonnet-5",
                    "content": [
                        {"type": "text", "text": f"Using key {ANTHROPIC_KEY} to search."},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": TOOL,
                            "input": {"query": "3.0 breaking changes", "name": "changelog"},
                        },
                    ],
                },
            },
        },
        {
            "role": "harness",
            "received_at": "2026-09-26T00:00:03Z",
            "harness_event": {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": {
                                "name": "Private customer",
                                "items": [{"name": "Another private customer"}],
                                "text": f"Authorization: {BEARER}\n{EXA_KEY} <elided:40 chars>",
                            },
                        }
                    ]
                },
            },
        },
        {
            "event": "final_answer",
            "role": "assistant",
            "timestamp": "2026-09-26T00:00:04Z",
            "content": "Found it: https://docs.example.test/changelog",
        },
        {"role": "telemetry", "usage": {"input_tokens": 1000, "output_tokens": 100}},
    ]
    (run_dir / "artifacts" / "transcript.json").write_text(json.dumps(transcript))
    (run_dir / "artifacts" / "harness-stderr.txt").write_text(
        f"debug: Cookie: {COOKIE}\nretrying with Authorization: {BEARER}\n"
    )
    (run_dir / "artifacts" / "final-answer.json").write_text(
        json.dumps({"answer": "4 breaking changes", "note": f"exa said {EXA_KEY}"})
    )
    spawn_path = run_dir / "artifacts" / "spawn-metadata.json"
    spawn = json.loads(spawn_path.read_text())
    spawn.update(
        {
            "env": {"SEW_EXA_API_KEY": "<redacted>", "LANG": "C"},
            "mcp_headers": {"x-api-key": EXA_KEY},
            "arm_contract": {
                "kind": "provider",
                "provider_id": "exa",
                "allowed_mcp_servers": ["exa"],
                "native_search": False,
            },
        }
    )
    spawn_path.write_text(json.dumps(spawn))
    call_path = run_dir / "provider-calls" / "search-1.json"
    call = json.loads(call_path.read_text())
    call["request"] = {
        "operation": "search",
        "headers": {"Authorization": BEARER, "Cookie": COOKIE},
        "note": f"key {ANTHROPIC_KEY}",
    }
    call_path.write_text(json.dumps(call))


def _assemble(root: Path, destination: Path, prices: Path, **options: Any) -> Any:
    return assemble_bundle(
        root,
        output_dir=destination,
        module_base=MODULE_ROOT,
        price_table_path=prices,
        generated_at=GENERATED_AT,
        environ=ENVIRON,
        **options,
    )


def _run_id(provider: str, repetition: int, task_id: str = FACT) -> str:
    return f"claude-code-{provider}-{task_id}-{repetition}"


def _dimensions(root: Path, run_id: str, **dimensions: bool) -> None:
    path = root / "bundles" / run_id / "evaluations" / "evaluation.json"
    evaluation = json.loads(path.read_text())
    evaluation["dimensions"].update(dimensions)
    path.write_text(json.dumps(evaluation))


def _arm_rows(markdown: str) -> dict[str, list[str]]:
    table = markdown.split("\n## Runs", 1)[0]
    rows = {}
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if line.startswith("| claude-code+"):
            rows[cells[0]] = cells[1:]
    return rows


def _placeholder_locations(bundle: Path) -> set[tuple[str, str]]:
    """Every (file, JSON pointer) in the bundle's evidence holding a placeholder."""

    found: set[tuple[str, str]] = set()
    for path in sorted(bundle.rglob("*")):
        rel = path.relative_to(bundle).as_posix()
        if not path.is_file() or rel == REDACTION_REPORT_NAME:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix in {".json", ".yaml"}:
            value = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
            found.update((rel, pointer) for pointer in _placeholder_pointers(value, ""))
        elif PLACEHOLDER_RE.search(text):
            found.add((rel, ""))
    return found


def _placeholder_pointers(value: Any, pointer: str) -> list[str]:
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in _placeholder_pointers(child, f"{pointer}/{key}")
        ]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _placeholder_pointers(child, f"{pointer}/{index}")
        ]
    if isinstance(value, str) and PLACEHOLDER_RE.search(value):
        return [pointer]
    return []


def _at(path: Path, pointer: str) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    for part in pointer.split("/")[1:]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value
