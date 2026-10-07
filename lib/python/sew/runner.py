"""Suite runner for Search Evaluation Workbench.

The runner owns matrix expansion, seeded order, budget gates, and resumable
state. Fixture execution is the default so tests and lighthouse smokes stay
offline; live mode must be given explicit operator budgets before it can even
start probing live-capable adapters, and then runs each hosted-harness cell
through ``LiveCellExecutor`` under that cell's own manifest budgets.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .arms import PROVIDER_SERVER_NAMES, prepare_arm_spawn
from . import broker_auth
from .catalog import module_root
from .harness import (
    AUTHORIZATION_HEADER_VALUE_RE,
    BEARER_RE,
    HarnessRunConfig,
    ProviderExposure,
    SECRET_KEY_RE,
    assert_no_secret_material,
    fixture_provider_exposure,
    run_fixture_harness,
    provider_availability_eligible,
)
from .live_harness import (
    BIN_ENV,
    DEFAULT_BIN,
    LIVE_ENV,
    PROVISIONAL_METERS_REF,
    live_enabled,
    run_live_harness,
)
from .pi_driver import PiHarnessDriver
from . import harnesses
from .mcp_meter import provider_available
from .providers import make_provider
from .schema import (
    SchemaError,
    load_document,
    load_record,
    load_suite_manifest,
    validate_fixture_run,
)
from .state import default_state_root
from .task_resolution import resolve_task

EXTERNAL_PROVIDERS = frozenset(
    {"exa", "parallel-web", "firecrawl", "brave", "tavily", "perplexity"}
)
# The harnesses sew.live_harness spawns for real, read live from the registry.
HOSTED_HARNESSES = harnesses.ids(live=True)
# Boot failures were retried as "failed" before WSB-05 split them out; keep that.
# provider_unavailable and budget_exhausted are not retried: an immediate retry
# into a quota wall or a spent budget only burns more of the budget.
RETRYABLE_STATUSES = frozenset({"failed", "timeout", "harness_boot_failed"})
# A run of provider_unavailable cells means a wall shared by the whole suite,
# such as the harness account's 429 on 2026-09-29, which failed 145 cells in five
# minutes. Stop resumably instead of recording every remaining cell as a failure.
PROVIDER_UNAVAILABLE_STOP_STREAK = 3
# A wall on one arm (a single harness account's usage limit) also forms streaks,
# and a resume puts the same deferred cells next to each other at the head of the
# queue. A cell deferred this many times is recorded provider_unavailable on its
# next unavailable run instead, so no wall can stop every resume on the same cells.
PROVIDER_UNAVAILABLE_MAX_DEFERRALS = 2


class RunnerError(RuntimeError):
    """Raised when a suite cannot be safely executed."""


@dataclass(frozen=True)
class OperatorBudgets:
    max_provider_calls: int
    max_provider_result_chars: int
    max_total_tokens: int
    max_wall_clock_seconds: int


@dataclass(frozen=True)
class MatrixCell:
    suite_id: str
    suite_version: str
    task_id: str
    provider_id: str
    harness_id: str
    model_profile: str
    repetition: int
    run_id: str
    required_operation: str
    applicable: bool
    not_applicable_reason: str | None = None

    @property
    def key(self) -> str:
        return "|".join(
            (
                self.task_id,
                self.provider_id,
                self.harness_id,
                self.model_profile,
                str(self.repetition),
            )
        )


@dataclass(frozen=True)
class CellExecution:
    status: str
    run_id: str
    run_dir: Path | None
    attempts: int = 1
    attempt_run_dirs: tuple[Path, ...] = ()
    failure_category: str | None = None
    stopped_reason: str | None = None


class CellExecutor(Protocol):
    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        """Execute one matrix cell and return terminal metadata."""


class FixtureCellExecutor:
    """Offline executor that writes normal fixture evidence bundles."""

    def __init__(self, *, module_base: Path | None = None) -> None:
        self._module_base = module_base or module_root()
        self._pi_driver: PiHarnessDriver | None = None

    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        if mode != "fixture":
            raise RunnerError("live suite execution is not implemented by fixture executor")
        if cell.harness_id in HOSTED_HARNESSES:
            external = (
                None
                if cell.provider_id == "native"
                else fixture_provider_exposure(cell.provider_id)
            )
            config = HarnessRunConfig(
                harness_id=cell.harness_id,  # type: ignore[arg-type]
                provider_id=cell.provider_id,
                task_id=cell.task_id,
                model_profile=cell.model_profile,
                suite_id=cell.suite_id,
                mode="fixture",
                external_provider=external,
                run_id_override=cell.run_id,
            )
            result = run_fixture_harness(config, output_root)
            return CellExecution(
                status=result.status,
                run_id=result.run_id,
                run_dir=result.bundle_dir,
                failure_category=result.failure_category,
            )
        if cell.harness_id == "pi":
            driver = self._pi_driver or PiHarnessDriver.from_config(
                self._module_base / "config" / "pi-model-profiles.yaml"
            )
            self._pi_driver = driver
            result = driver.run_fixture_task(
                output_root,
                profile=driver.profile(cell.model_profile),
                provider_id=cell.provider_id,
                task_id=cell.task_id,
                suite_id=cell.suite_id,
                run_id=cell.run_id,
            )
            return CellExecution(status=result.status, run_id=result.run_id, run_dir=result.run_dir)
        raise RunnerError(f"unknown harness: {cell.harness_id}")


LIVE_HARNESS_UNSUPPORTED = "live_harness_unsupported"


@dataclass(frozen=True)
class CellBudgets:
    """One cell's own spend limits, read from its task manifest."""

    wall_clock_seconds: int
    max_provider_calls: int
    max_total_tokens: int

    @classmethod
    def from_task(cls, task: Mapping[str, Any]) -> CellBudgets:
        budgets = task["budgets"]
        return cls(
            wall_clock_seconds=int(budgets["wall_clock_seconds"]),
            max_provider_calls=int(budgets["max_provider_calls"]),
            max_total_tokens=int(budgets["max_total_tokens"]),
        )


class LiveCellExecutor:
    """Runs hosted-harness cells for real, each under its own manifest budgets.

    A task manifest's budgets become hard limits on the harness it spawns:
    ``wall_clock_seconds`` is the timeout, ``max_total_tokens`` caps every
    token the harness processes (cached input and boot context included, see
    ``live_harness.usage_total``), and ``max_provider_calls`` caps its search
    calls. Running out of wall clock ends the cell as ``timeout``; running out
    of tokens or provider calls ends it as ``budget_exhausted``. Neither is
    ``failed``, which is reserved for a harness that ran and did not deliver.

    Pi has no live cell path (WSB benchmarks Claude Code and Codex only), so a
    Pi cell is recorded not applicable instead of spawned.
    """

    def __init__(
        self,
        *,
        module_base: Path | None = None,
        provider_exposures: Mapping[str, ProviderExposure] | None = None,
        environ: Mapping[str, str] | None = None,
        env: Mapping[str, str] | None = None,
        harness_auth: str | None = None,
    ) -> None:
        self._module_base = module_base or module_root()
        self._exposures = dict(provider_exposures or {})
        self._environ = environ
        # Extra child environment for every spawned harness.
        self._env = dict(env or {})
        self._harness_auth = harness_auth
        self._tasks: dict[str, dict[str, Any]] = {}

    def preflight(self, cells: Sequence[MatrixCell], *, mode: str) -> None:
        """Refuse a live run that cannot finish, before any cell spends money.

        Each arm's spawn surface is materialized once here, so a provider
        config the arm contract rejects stops the run now rather than at its
        first cell, possibly hours in.
        """

        environ = self._source_environ()
        if not live_enabled(environ):
            raise RunnerError(
                f"live suite execution is operator-gated; set {LIVE_ENV}=1 to spawn real "
                "harnesses (CI never sets it)"
            )
        hosted = [c for c in cells if c.applicable and c.harness_id in HOSTED_HARNESSES]
        problems = []
        for harness_id in sorted({cell.harness_id for cell in hosted}):
            binary = environ.get(BIN_ENV[harness_id]) or DEFAULT_BIN[harness_id]
            if shutil.which(binary, path=environ.get("PATH")) is None:
                problems.append(
                    f"{harness_id} binary {binary!r} is not executable (set {BIN_ENV[harness_id]})"
                )
        unconfigured = sorted(
            {
                cell.provider_id
                for cell in hosted
                if cell.provider_id in PROVIDER_SERVER_NAMES
                and cell.provider_id not in self._exposures
            }
        )
        if unconfigured:
            problems.append(
                f"provider arms without an MCP server config: {', '.join(unconfigured)} "
                "(pass --provider-mcp-config)"
            )
        profiles = sorted(
            {f"{c.harness_id}:{c.model_profile}" for c in hosted if c.model_profile != "default"}
        )
        if profiles:
            problems.append(
                "a live hosted harness runs its configured model, so only model_profile "
                f"'default' is accepted; got {', '.join(profiles)}"
            )
        if problems:
            raise RunnerError("live run refused before any cell ran: " + "; ".join(problems))
        arms = {(cell.harness_id, cell.provider_id): cell for cell in hosted}
        for cell in arms.values():
            with tempfile.TemporaryDirectory(prefix="sew-preflight-") as scratch:
                try:
                    prepare_arm_spawn(
                        self._harness_config(cell),
                        Path(scratch),
                        environ,
                        harness_auth=broker_auth.auth_source(self._harness_auth, environ),
                    )
                except SchemaError as exc:
                    raise RunnerError(
                        f"live run refused: arm {cell.harness_id}+{cell.provider_id}: {exc}"
                    ) from exc

    def execute(self, cell: MatrixCell, output_root: Path, *, mode: str) -> CellExecution:
        if mode != "live":
            raise RunnerError("the live cell executor only runs mode='live'")
        if cell.harness_id not in HOSTED_HARNESSES:
            # Emit the terminal contract status directly rather than relying on
            # normalize_runner_status to map "unsupported" at record time.
            return CellExecution(
                status="not_applicable",
                run_id=cell.run_id,
                run_dir=None,
                failure_category=LIVE_HARNESS_UNSUPPORTED,
            )
        adopted = adopt_completed_bundle(cell, output_root)
        if adopted is not None:
            return adopted
        budgets = CellBudgets.from_task(self._task(cell.task_id))
        if budgets.max_total_tokens <= 0:
            # A cell allowed no tokens is spent before it starts, the way a zero
            # wall clock is timed out before it starts.
            return CellExecution(
                status="budget_exhausted",
                run_id=cell.run_id,
                run_dir=None,
                failure_category="token_budget_exceeded",
            )
        config = self._harness_config(cell, budgets=budgets)
        result = run_live_harness(config, output_root, environ=self._source_environ())
        return CellExecution(
            status=result.status,
            run_id=result.run_id,
            run_dir=result.bundle_dir,
            failure_category=result.failure_category,
        )

    def _harness_config(
        self, cell: MatrixCell, *, budgets: CellBudgets | None = None
    ) -> HarnessRunConfig:
        exposure = None
        if cell.provider_id in PROVIDER_SERVER_NAMES:
            exposure = self._exposures.get(cell.provider_id)
            if exposure is None:
                raise RunnerError(f"provider arm {cell.provider_id!r} has no MCP server config")
        resolved = resolve_task(cell.task_id, self._module_base)
        return HarnessRunConfig(
            harness_id=cell.harness_id,  # type: ignore[arg-type]
            provider_id=cell.provider_id,
            task_id=cell.task_id,
            model_profile=cell.model_profile,
            suite_id=cell.suite_id,
            mode="live",
            native_search_available=cell.provider_id == "native",
            external_provider=exposure,
            prompt_text=resolved.prompt if budgets else None,
            task_source=resolved.source,
            run_id_override=cell.run_id,
            env=dict(self._env),
            harness_auth=self._harness_auth,
            timeout_seconds=float(budgets.wall_clock_seconds) if budgets else None,
            max_total_tokens=budgets.max_total_tokens if budgets else None,
            max_provider_calls=budgets.max_provider_calls if budgets else None,
        )

    def _task(self, task_id: str) -> dict[str, Any]:
        if task_id not in self._tasks:
            self._tasks[task_id] = resolve_task(task_id, self._module_base).task
        return self._tasks[task_id]

    def _source_environ(self) -> dict[str, str]:
        return dict(os.environ if self._environ is None else self._environ)


def _complete_live_bundle(cell: MatrixCell, run_dir: Path) -> dict[str, Any] | None:
    """``run.json`` of a complete, secret-free live bundle of ``cell``, else None."""

    try:
        validate_fixture_run(run_dir)
        assert_no_secret_material(run_dir)
        run = load_document(run_dir / "run.json")
    except (OSError, SchemaError):
        return None
    identity = tuple(
        run.get(key)
        for key in ("suite_id", "task_id", "provider_id", "harness_id", "model_profile")
    )
    expected = (cell.suite_id, cell.task_id, cell.provider_id, cell.harness_id, cell.model_profile)
    if run.get("mode") != "live" or run.get("run_id") != run_dir.name or identity != expected:
        return None
    if (
        cell.provider_id in PROVIDER_SERVER_NAMES
        and provider_availability_eligible(run.get("status"), run.get("failure_category"))
        and provider_available(run_dir, cell.provider_id, run["run_id"]) is False
    ):
        # A pre-GAPMCP bundle may have completed before its index was saved.
        # Adoption must enforce the same discovery gate as a fresh live cell.
        run["status"] = "provider_unavailable"
        run["failure_category"] = "provider_tools_unavailable"
        atomic_write_json(run_dir / "run.json", run)
    return run


def adopt_completed_bundle(cell: MatrixCell, output_root: Path) -> CellExecution | None:
    """The complete live bundle a cell already wrote, if a crash lost its index entry.

    The runner indexes a cell only after its bundle is written, so a process
    that dies in between leaves a finished, paid-for cell the index does not
    know about. Running it again would execute a completed cell twice. The
    newest complete bundle under the cell's run id, or a ``-rN`` successor
    (see ``live_harness._claim_run_dir``), is adopted instead. A directory an
    interrupted spawn left behind is not a complete bundle, so that cell runs
    again under the next free id.
    """

    pattern = re.compile(rf"{re.escape(cell.run_id)}(?:-r(\d+))?")
    candidates = []
    for path in (output_root / cell.run_id, *output_root.glob(f"{cell.run_id}-r*")):
        match = pattern.fullmatch(path.name)
        if match is not None and path.is_dir():
            candidates.append((int(match.group(1) or 1), path))
    for _, run_dir in sorted(candidates, reverse=True):
        run = _complete_live_bundle(cell, run_dir)
        if run is None:
            continue
        if run.get("status") == "provider_unavailable":
            # The bundle holds no usable answer, so the cell runs again under the
            # next free id (runner streak stop). unavailable_bundles hands it to
            # the cell's index entry, and absorb_run_dirs charges its spend.
            continue
        return CellExecution(
            status=str(run["status"]),
            run_id=run_dir.name,
            run_dir=run_dir,
            failure_category=run.get("failure_category"),
        )
    return None


def unavailable_bundles(
    cell: MatrixCell, output_root: Path, known: Iterable[Path] = ()
) -> list[Path]:
    """Complete live ``provider_unavailable`` bundles of any attempt of ``cell``, oldest first.

    adopt_completed_bundle skips these bundles, so a cell with one runs again.
    The runner lists them in that cell's ``attempt_run_dirs``, so the evidence
    stays with the cell after a streak stop or a crash before indexing.
    Paths in ``known`` (resolved) are already recorded and are not re-validated.
    """

    skip = set(known)
    pattern = re.compile(rf"{re.escape(cell.run_id)}(?:-att(\d+))?(?:-r(\d+))?")
    candidates = []
    for path in output_root.glob(f"{cell.run_id}*"):
        match = pattern.fullmatch(path.name)
        if match is None or not path.is_dir() or path.resolve(strict=False) in skip:
            continue
        run = _complete_live_bundle(cell, path)
        if run is not None and run.get("status") == "provider_unavailable":
            candidates.append((int(match.group(1) or 1), int(match.group(2) or 1), path))
    return [path for _, _, path in sorted(candidates)]


_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_ARG_ENV_NAME_RE = re.compile(r"(?:^|_)(?:URL|ENDPOINT|HOST|PATH|DIR)$", re.IGNORECASE)
_ARG_AUTH_NAME_RE = re.compile(r"(?:^|_)(?:KEY|AUTH|HEADER|PAT)(?:_|$)", re.IGNORECASE)
_ARG_CREDENTIAL_VALUE_RE = re.compile(
    r"(?i)\b(?:basic|token)\s+\S+|"
    r"\b(?:api[_-]?key|password|credential|secret|token)\s*[:=]\s*\S+"
)


def load_provider_exposures(
    path: Path, environ: Mapping[str, str] | None = None
) -> dict[str, ProviderExposure]:
    """Provider-arm MCP servers for live cells, keyed by provider id.

    The file (YAML or JSON) maps a provider id to the MCP server config that
    arm exposes::

        exa:
          command: npx
          args: ["-y", "exa-mcp-server"]
          env: {EXA_API_KEY: "${SEW_EXA_API_KEY}"}

    ``${NAME}`` is read from the environment at load time, so the file holds
    references rather than credentials. Args references must name a non-secret
    URL, ENDPOINT, HOST, PATH or DIR; credential-shaped values are also refused.
    Use env or header_from_env for credentials. An unset reference
    refuses the run: an MCP server started without its key fails every call,
    which would read as a
    provider that found nothing.
    """

    source = os.environ if environ is None else environ
    doc = load_document(path)
    if not isinstance(doc, Mapping) or not doc:
        raise RunnerError(f"{path}: expected a mapping of provider id to MCP server config")
    exposures = {}
    for provider_id, server_config in doc.items():
        if provider_id not in PROVIDER_SERVER_NAMES:
            raise RunnerError(
                f"{path}: unknown provider arm {provider_id!r}; "
                f"expected one of {', '.join(sorted(PROVIDER_SERVER_NAMES))}"
            )
        if not isinstance(server_config, Mapping) or not server_config:
            raise RunnerError(f"{path}: {provider_id} needs a non-empty MCP server config")
        server = PROVIDER_SERVER_NAMES[provider_id]
        from .pricing_coverage import configured_pricing_tools, require_pricing_parity

        resolved_config = _resolve_env_refs(server_config, source, f"{path}: {provider_id}")
        try:
            require_pricing_parity(
                {provider_id: configured_pricing_tools(provider_id, resolved_config)}
            )
        except ValueError as exc:
            raise RunnerError(f"{path}: {exc}") from exc
        exposures[provider_id] = ProviderExposure(
            provider_id=provider_id,
            tool_name=f"mcp__{server}__*",
            mcp_server_name=server,
            mcp_server_config=resolved_config,
        )
    return exposures


def _resolve_env_refs(
    value: Any, environ: Mapping[str, str], where: str, *, in_args: bool = False
) -> Any:
    if isinstance(value, str):

        def _lookup(match: re.Match[str]) -> str:
            name = match.group(1)
            if in_args and (
                not _ARG_ENV_NAME_RE.search(name)
                or SECRET_KEY_RE.search(name)
                or _ARG_AUTH_NAME_RE.search(name)
            ):
                raise RunnerError(
                    f"{where} refuses reference ${{{name}}} in args; "
                    "only non-secret URL/ENDPOINT/HOST/PATH/DIR references are allowed; "
                    "use env or header_from_env with --header-file instead"
                )
            if not environ.get(name):
                raise RunnerError(f"{where} references ${{{name}}}, which is unset")
            return environ[name]

        resolved = _ENV_REF_RE.sub(_lookup, value)
        if in_args and any(
            pattern.search(resolved)
            for pattern in (BEARER_RE, AUTHORIZATION_HEADER_VALUE_RE, _ARG_CREDENTIAL_VALUE_RE)
        ):
            raise RunnerError(
                f"{where} refuses credential-shaped value in args; "
                "use env or header_from_env with --header-file instead"
            )
        return resolved
    if isinstance(value, Mapping):
        return {
            str(key): _resolve_env_refs(
                item, environ, f"{where}.{key}", in_args=in_args or key == "args"
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_env_refs(item, environ, f"{where}[{index}]", in_args=in_args)
            for index, item in enumerate(value)
        ]
    return value


class SuiteRunner:
    def __init__(
        self,
        *,
        module_base: Path | None = None,
        state_root: Path | None = None,
        executor: CellExecutor | None = None,
        live_executor: CellExecutor | None = None,
    ) -> None:
        self.module_base = module_base or module_root()
        self.state_root = state_root or default_state_root()
        self.executor = executor or FixtureCellExecutor(module_base=self.module_base)
        # An injected executor serves both modes, as it always has. Otherwise
        # live mode gets a LiveCellExecutor, built on first use.
        self.live_executor = live_executor or executor

    def dry_run(
        self,
        suite_id: str = "lighthouse",
        *,
        repetitions: int | None = None,
        seed: int | None = None,
        mode: str = "fixture",
    ) -> dict[str, Any]:
        suite, tasks = self._load_suite(suite_id)
        cells = self.expand_matrix(suite, tasks, repetitions=repetitions, seed=seed, mode=mode)
        return {
            "suite_id": suite["suite_id"],
            "seed": seed if seed is not None else suite["randomization_seed"],
            "total_cells": len(cells),
            "scheduled_cells": sum(1 for cell in cells if cell.applicable),
            "not_applicable_cells": sum(1 for cell in cells if not cell.applicable),
            "cells": [cell_asdict(cell) for cell in cells],
        }

    def run(
        self,
        suite_id: str = "lighthouse",
        *,
        mode: str = "fixture",
        repetitions: int | None = None,
        seed: int | None = None,
        run_id: str | None = None,
        resume: bool = True,
        max_cells: int | None = None,
        operator_budgets: OperatorBudgets | None = None,
    ) -> dict[str, Any]:
        suite, tasks = self._load_suite(suite_id)
        if mode == "live" and operator_budgets is None:
            raise RunnerError("live mode requires explicit operator budgets")
        cells = self.expand_matrix(suite, tasks, repetitions=repetitions, seed=seed, mode=mode)
        executor = self._executor_for(mode)
        preflight = getattr(executor, "preflight", None)
        if callable(preflight):
            preflight(cells, mode=mode)
        resolved_seed = seed if seed is not None else suite["randomization_seed"]
        suite_run_id = validate_suite_run_id(
            run_id or stable_suite_run_id(suite, seed=seed, repetitions=repetitions, mode=mode)
        )
        run_root = self.state_root / ".sew" / "runs" / suite_run_id
        output_root = run_root / "bundles"
        if not resume and run_root.exists():
            shutil.rmtree(run_root)
        run_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)
        state = self._load_or_create_state(
            run_root, suite, cells, resume=resume, seed=resolved_seed, mode=mode
        )
        index: list[dict[str, Any]] = state.setdefault("index", [])
        completed_keys = {
            str(entry["cell_key"])
            for entry in index
            if entry.get("terminal") is True and entry.get("cell_key")
        }
        budgets = BudgetTracker.from_suite(suite, operator_budgets)
        budgets.restore_elapsed_seconds(state.get("budget_elapsed_seconds"))
        budgets.absorb_index(index)
        budgets.absorb_run_dirs(output_root, cells)

        executed_this_call = 0
        stopped_reason = None
        # A streak carries across calls: a crash or stop between its cells
        # leaves them indexed, and the next unavailable cell still defers them.
        unavailable_streak = trailing_unavailable_streak(index)
        deferred: dict[str, dict[str, Any]] = state.setdefault("deferred_unavailable", {})
        for cell in cells:
            if cell.key in completed_keys:
                continue
            if max_cells is not None and executed_this_call >= max_cells:
                stopped_reason = "interrupted"
                break
            if not cell.applicable:
                index.append(self._index_entry(cell, "not_applicable", terminal=True))
                completed_keys.add(cell.key)
                self._persist_progress(run_root, state, index, len(cells), None, budgets)
                continue
            timeout_reason = self._timeout_reason(suite, tasks[cell.task_id])
            if timeout_reason is not None:
                index.append(
                    self._index_entry(
                        cell,
                        "timeout",
                        terminal=True,
                        failure_category=timeout_reason,
                    )
                )
                completed_keys.add(cell.key)
                executed_this_call += 1
                self._persist_progress(run_root, state, index, len(cells), None, budgets)
                continue
            budget_reason = budgets.can_start(tasks[cell.task_id])
            if budget_reason is not None:
                stopped_reason = budget_reason
                break

            # Walled bundles this cell already wrote: earlier streak stops record
            # them, and a crash before indexing leaves them on disk only.
            deferral = deferred.pop(cell.key, None)
            earlier_run_dirs = list((deferral or {}).get("attempt_run_dirs", []))
            known = {Path(path).resolve(strict=False) for path in earlier_run_dirs}
            earlier_run_dirs += [
                str(path) for path in unavailable_bundles(cell, output_root, known)
            ]
            started = time.monotonic()
            try:
                execution = self._execute_with_retries(
                    executor, cell, output_root, mode, suite, tasks[cell.task_id], budgets
                )
            except BaseException:
                if deferral is not None:
                    deferred[cell.key] = deferral
                state["index"] = index
                state["budget_elapsed_seconds"] = round(budgets.elapsed_seconds(), 6)
                state["summary"] = summarize_index(index, len(cells), stopped_reason, deferred)
                self._write_state(run_root, state)
                raise
            elapsed = time.monotonic() - started
            entry = self._entry_from_execution(cell, execution, elapsed)
            if earlier_run_dirs:
                # The walled bundles stay with the cell's entry, so the index
                # keeps their evidence and charges their spend.
                entry["attempt_run_dirs"] = [
                    *earlier_run_dirs,
                    *entry.get("attempt_run_dirs", []),
                ]
            if deferral is not None:
                # The deferral record is popped: deferred_unavailable lists only
                # cells still out of the index, and the entry keeps the count.
                entry["unavailable_deferrals"] = deferral["deferrals"]
            index.append(entry)
            completed_keys.add(cell.key)
            executed_this_call += 1
            post_reason = execution.stopped_reason or budgets.exhausted_reason()
            if entry.get("status") != "provider_unavailable":
                unavailable_streak = []
            elif entry.get("unavailable_deferrals", 0) < PROVIDER_UNAVAILABLE_MAX_DEFERRALS:
                unavailable_streak.append(entry)
            # A cell out of deferrals is recorded as provider_unavailable. It
            # neither extends nor breaks the streak.
            if len(unavailable_streak) >= PROVIDER_UNAVAILABLE_STOP_STREAK:
                # Unindex the streak so a resume runs those cells again; their
                # bundles are not adopted (adopt_completed_bundle skips them).
                for failed in unavailable_streak:
                    index.remove(failed)
                    failed_key = str(failed["cell_key"])
                    completed_keys.discard(failed_key)
                    deferred[failed_key] = {
                        "deferrals": failed.get("unavailable_deferrals", 0) + 1,
                        "attempt_run_dirs": list(failed.get("attempt_run_dirs", [])),
                    }
                # A spent budget is the binding limit: resuming after the wall
                # clears would stop again at once.
                stopped_reason = post_reason or "provider_unavailable"
                self._persist_progress(run_root, state, index, len(cells), stopped_reason, budgets)
                break
            if post_reason is not None:
                stopped_reason = post_reason
            self._persist_progress(run_root, state, index, len(cells), stopped_reason, budgets)
            if post_reason is not None:
                break

        state["index"] = index
        state["budget_elapsed_seconds"] = round(budgets.elapsed_seconds(), 6)
        state["summary"] = summarize_index(index, len(cells), stopped_reason, deferred)
        self._write_state(run_root, state)
        return {
            "suite_run_id": suite_run_id,
            "run_root": str(run_root),
            "output_root": str(output_root),
            # The limits that applied, after suite ceilings: an operator cap
            # above a committed suite budget is reported at the suite's value.
            "budget_limits": asdict(budgets.limits),
            "budget_elapsed_seconds": state["budget_elapsed_seconds"],
            **state["summary"],
        }

    def expand_matrix(
        self,
        suite: dict[str, Any],
        tasks: dict[str, dict[str, Any]],
        *,
        repetitions: int | None = None,
        seed: int | None = None,
        mode: str = "fixture",
    ) -> list[MatrixCell]:
        reps = repetitions if repetitions is not None else suite["repetitions"]
        random_seed = seed if seed is not None else suite["randomization_seed"]
        provider_capabilities = self._provider_capabilities(suite)
        pi_driver = PiHarnessDriver.from_config(
            self.module_base / "config" / "pi-model-profiles.yaml"
        )
        by_class: dict[str, list[MatrixCell]] = {}
        for task_id in suite["tasks"]:
            task = tasks[task_id]
            operation = required_provider_operation(task)
            for provider_id in suite["providers"]:
                for harness_id, harness_config in suite["harnesses"].items():
                    for model_profile in harness_config["model_profiles"]:
                        for repetition in range(1, reps + 1):
                            applicable, reason = cell_applicability(
                                task,
                                mode,
                                provider_id,
                                harness_id,
                                model_profile,
                                operation,
                                provider_capabilities,
                                pi_driver,
                            )
                            cell = MatrixCell(
                                suite_id=suite["suite_id"],
                                suite_version=str(suite["version"]),
                                task_id=task_id,
                                provider_id=provider_id,
                                harness_id=harness_id,
                                model_profile=model_profile,
                                repetition=repetition,
                                run_id=stable_run_id(
                                    suite["suite_id"],
                                    str(suite["version"]),
                                    task_id,
                                    provider_id,
                                    harness_id,
                                    model_profile,
                                    repetition,
                                ),
                                required_operation=operation,
                                applicable=applicable,
                                not_applicable_reason=reason,
                            )
                            by_class.setdefault(task["task_class"], []).append(cell)

        rand = random.Random(random_seed)
        for class_cells in by_class.values():
            rand.shuffle(class_cells)

        class_queues = {k: deque(v) for k, v in by_class.items()}
        cells: list[MatrixCell] = []
        while class_queues:
            for k in sorted(class_queues.keys()):
                if class_queues[k]:
                    cells.append(class_queues[k].popleft())
            class_queues = {k: v for k, v in class_queues.items() if v}

        return cells

    def _execute_with_retries(
        self,
        executor: CellExecutor,
        cell: MatrixCell,
        output_root: Path,
        mode: str,
        suite: dict[str, Any],
        task: dict[str, Any],
        budgets: BudgetTracker,
    ) -> CellExecution:
        max_attempts = max_attempts_for(suite, task)
        attempts = 1
        last = executor.execute(attempt_cell(cell, attempts), output_root, mode=mode)
        attempt_run_dirs = [last.run_dir] if last.run_dir is not None else []
        budgets.absorb_run_dir(last.run_dir)
        stopped_reason = None
        while last.status in RETRYABLE_STATUSES and attempts < max_attempts:
            stopped_reason = budgets.can_start(task)
            if stopped_reason is not None:
                break
            attempts += 1
            last = executor.execute(attempt_cell(cell, attempts), output_root, mode=mode)
            if last.run_dir is not None:
                attempt_run_dirs.append(last.run_dir)
            budgets.absorb_run_dir(last.run_dir)
        return CellExecution(
            status=last.status,
            run_id=last.run_id,
            run_dir=last.run_dir,
            attempts=attempts,
            attempt_run_dirs=tuple(attempt_run_dirs),
            failure_category=last.failure_category,
            stopped_reason=stopped_reason,
        )

    def _executor_for(self, mode: str) -> CellExecutor:
        if mode != "live":
            return self.executor
        if self.live_executor is None:
            self.live_executor = LiveCellExecutor(module_base=self.module_base)
        return self.live_executor

    def _load_suite(self, suite_id: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        suite = load_suite_manifest(self.module_base / "catalogs" / suite_id)
        tasks = {
            task_id: resolve_task(task_id, self.module_base).task for task_id in suite["tasks"]
        }
        return suite, tasks

    def _provider_capabilities(self, suite: dict[str, Any]) -> dict[str, frozenset[str]]:
        capabilities = {"native": frozenset({"native"})}
        for provider_id in suite["providers"]:
            if provider_id in EXTERNAL_PROVIDERS:
                provider = make_provider(provider_id, live_enabled=False)
                capabilities[provider_id] = provider.capabilities.operations
        return capabilities

    def _load_or_create_state(
        self,
        run_root: Path,
        suite: dict[str, Any],
        cells: list[MatrixCell],
        *,
        resume: bool,
        seed: int,
        mode: str = "fixture",
    ) -> dict[str, Any]:
        state_path = run_root / "runner-state.json"
        order = [cell.key for cell in cells]
        if resume and state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("order") != order:
                raise RunnerError("resume refused: suite order changed")
            # A run is all fixture or all live: finishing a fixture run with
            # live cells would mix canned and real evidence under one run id.
            # State written before WSB-07 carries no mode; live mode raised then.
            started_mode = state.get("mode", "fixture")
            if started_mode != mode:
                raise RunnerError(
                    f"resume refused: run was started in {started_mode} mode, not {mode}"
                )
            return state
        return {
            "schema_version": 1,
            "suite_id": suite["suite_id"],
            "suite_version": str(suite["version"]),
            "seed": seed,
            "mode": mode,
            "order": order,
            "index": [],
        }

    def _write_state(self, run_root: Path, state: dict[str, Any]) -> None:
        path = run_root / "runner-state.json"
        atomic_write_json(path, state)
        index_path = run_root / "run-index.json"
        atomic_write_json(index_path, state.get("index", []))

    def _persist_progress(
        self,
        run_root: Path,
        state: dict[str, Any],
        index: list[dict[str, Any]],
        total_cells: int,
        stopped_reason: str | None,
        budgets: "BudgetTracker" | None = None,
    ) -> None:
        state["index"] = index
        if budgets is not None:
            state["budget_elapsed_seconds"] = round(budgets.elapsed_seconds(), 6)
        state["summary"] = summarize_index(
            index, total_cells, stopped_reason, state.get("deferred_unavailable")
        )
        self._write_state(run_root, state)

    def _timeout_reason(self, suite: dict[str, Any], task: dict[str, Any]) -> str | None:
        if task["budgets"]["wall_clock_seconds"] <= 0:
            return "task_timeout_before_start"
        return None

    def _entry_from_execution(
        self, cell: MatrixCell, execution: CellExecution, elapsed_seconds: float
    ) -> dict[str, Any]:
        entry = self._index_entry(
            cell,
            normalize_runner_status(execution.status),
            terminal=True,
            run_dir=str(execution.run_dir) if execution.run_dir else None,
            attempts=execution.attempts,
            failure_category=execution.failure_category,
        )
        entry["run_id"] = execution.run_id
        entry["elapsed_seconds"] = round(elapsed_seconds, 6)
        if execution.attempt_run_dirs:
            entry["attempt_run_dirs"] = [str(path) for path in execution.attempt_run_dirs]
        return entry

    def _index_entry(
        self,
        cell: MatrixCell,
        status: str,
        *,
        terminal: bool,
        run_dir: str | None = None,
        attempts: int = 0,
        failure_category: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "cell_key": cell.key,
            "run_id": cell.run_id,
            "task_id": cell.task_id,
            "provider_id": cell.provider_id,
            "harness_id": cell.harness_id,
            "model_profile": cell.model_profile,
            "repetition": cell.repetition,
            "status": status,
            "terminal": terminal,
            "attempts": attempts,
        }
        if cell.not_applicable_reason:
            entry["not_applicable_reason"] = cell.not_applicable_reason
        if run_dir is not None:
            entry["run_dir"] = run_dir
        if failure_category is not None:
            entry["failure_category"] = failure_category
        return entry


class BudgetTracker:
    def __init__(self, limits: OperatorBudgets) -> None:
        self.limits = limits
        self.start_time = time.monotonic()
        self.elapsed_before_start = 0.0
        self.provider_calls = 0
        self.provider_result_chars = 0
        self.total_tokens = 0
        self._charged_run_dirs: set[Path] = set()

    @classmethod
    def from_suite(
        cls, suite: dict[str, Any], operator_budgets: OperatorBudgets | None
    ) -> "BudgetTracker":
        """Suite-wide limits for one run.

        The suite's `budgets` are committed spend ceilings: an operator cap can
        lower them, never raise them. Wall time is not spend, so an operator's
        `max_wall_clock_seconds` (always present in live mode) is the run's wall
        clock as given; the suite's `timeouts.run_seconds` is only the fallback
        for a run without operator caps. Per-cell limits come from each task's
        catalog budgets, not from either value.
        """
        raw = suite["budgets"]
        suite_limits = OperatorBudgets(
            max_provider_calls=raw["max_provider_calls"],
            max_provider_result_chars=raw["max_provider_result_chars"],
            max_total_tokens=raw["max_total_tokens"],
            max_wall_clock_seconds=suite["timeouts"]["run_seconds"],
        )
        if operator_budgets is None:
            return cls(suite_limits)
        return cls(
            OperatorBudgets(
                max_provider_calls=min(
                    suite_limits.max_provider_calls, operator_budgets.max_provider_calls
                ),
                max_provider_result_chars=min(
                    suite_limits.max_provider_result_chars,
                    operator_budgets.max_provider_result_chars,
                ),
                max_total_tokens=min(
                    suite_limits.max_total_tokens, operator_budgets.max_total_tokens
                ),
                max_wall_clock_seconds=operator_budgets.max_wall_clock_seconds,
            )
        )

    def can_start(self, task: dict[str, Any]) -> str | None:
        if self._wall_clock_exhausted():
            return "wall_clock_budget_exhausted"
        budgets = task["budgets"]
        if self.provider_calls + budgets["max_provider_calls"] > self.limits.max_provider_calls:
            return "provider_call_budget_exhausted"
        if (
            self.provider_result_chars + budgets["max_bytes"]
            > self.limits.max_provider_result_chars
        ):
            return "provider_result_character_budget_exhausted"
        if self.total_tokens + budgets["max_total_tokens"] > self.limits.max_total_tokens:
            return "token_budget_exhausted"
        return None

    def exhausted_reason(self) -> str | None:
        if self._wall_clock_exhausted():
            return "wall_clock_budget_exhausted"
        if self.provider_calls >= self.limits.max_provider_calls:
            return "provider_call_budget_exhausted"
        if self.provider_result_chars >= self.limits.max_provider_result_chars:
            return "provider_result_character_budget_exhausted"
        if self.total_tokens >= self.limits.max_total_tokens:
            return "token_budget_exhausted"
        return None

    def absorb_index(self, index: list[dict[str, Any]]) -> None:
        for entry in index:
            attempt_run_dirs = entry.get("attempt_run_dirs")
            if isinstance(attempt_run_dirs, list):
                for attempt_run_dir in attempt_run_dirs:
                    if isinstance(attempt_run_dir, str):
                        self.absorb_run_dir(Path(attempt_run_dir))
                continue
            run_dir = entry.get("run_dir")
            if isinstance(run_dir, str):
                self.absorb_run_dir(Path(run_dir))

    def absorb_run_dirs(self, output_root: Path, cells: Sequence[MatrixCell]) -> None:
        for cell in cells:
            pattern = re.compile(rf"{re.escape(cell.run_id)}(?:-att\d+)?(?:-r\d+)?")
            for path in output_root.glob(f"{cell.run_id}*"):
                if path.is_dir() and pattern.fullmatch(path.name):
                    self.absorb_run_dir(path)

    def absorb_run_dir(self, run_dir: Path | None) -> None:
        if run_dir is None or not run_dir.exists():
            return
        key = run_dir.resolve(strict=False)
        if key in self._charged_run_dirs:
            return
        charged = False
        metered_calls = 0
        metered_tokens = 0
        metered_result_chars = 0
        try:
            run = load_record(run_dir, "run.json")
            metrics = load_record(run_dir, run["metrics_ref"])
        except (FileNotFoundError, KeyError, OSError, SchemaError):
            metrics = None
        if isinstance(metrics, Mapping):
            charged = True
            provider_calls = metrics.get("provider_calls") or {}
            if isinstance(provider_calls, Mapping):
                metered_calls = _meter(provider_calls.get("total"))
            metered_result_chars = _meter(metrics.get("provider_result_chars"))
            usage = metrics.get("token_usage") or {}
            if isinstance(usage, Mapping):
                for token_bucket in ("input", "cached_input", "output", "reasoning"):
                    metered_tokens += _meter(usage.get(token_bucket))
        observed_calls, observed_tokens = live_process_meters(run_dir)
        provisional_calls, provisional_tokens = live_provisional_meters(run_dir)
        charged = charged or observed_calls > 0 or observed_tokens > 0
        charged = charged or provisional_calls > 0 or provisional_tokens > 0
        if not charged:
            return
        self.provider_calls += max(metered_calls, observed_calls, provisional_calls)
        self.provider_result_chars += metered_result_chars
        self.total_tokens += max(metered_tokens, observed_tokens, provisional_tokens)
        self._charged_run_dirs.add(key)

    def restore_elapsed_seconds(self, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return
        self.elapsed_before_start = max(0.0, float(value))
        self.start_time = time.monotonic()

    def elapsed_seconds(self) -> float:
        return self.elapsed_before_start + max(0.0, time.monotonic() - self.start_time)

    def _wall_clock_exhausted(self) -> bool:
        return self.elapsed_seconds() >= self.limits.max_wall_clock_seconds


def live_process_meters(run_dir: Path) -> tuple[int, int]:
    """``(search calls, tokens)`` the live driver metered while the harness ran.

    A live bundle writes no provider-call records, so its metrics count zero
    provider calls, and a cell killed mid-stream never receives the harness's
    closing usage row, so its token usage is unknown. The spend still
    happened: the driver's process meters in spawn metadata are the floor the
    suite budget charges. Fixture bundles carry no meters and read as zero.
    """

    try:
        spawn = load_record(run_dir, "artifacts/spawn-metadata.json")
    except (OSError, SchemaError):
        return 0, 0
    process = spawn.get("process") if isinstance(spawn, Mapping) else None
    if not isinstance(process, Mapping):
        return 0, 0
    return _meter(process.get("provider_calls")), _meter(process.get("running_tokens"))


def live_provisional_meters(run_dir: Path) -> tuple[int, int]:
    """``(search calls, tokens)`` from an interrupted live cell snapshot."""

    try:
        meters = load_record(run_dir, PROVISIONAL_METERS_REF)
    except (OSError, SchemaError):
        return 0, 0
    process = meters.get("process") if isinstance(meters, Mapping) else None
    if not isinstance(process, Mapping):
        return 0, 0
    return _meter(process.get("provider_calls")), _meter(process.get("running_tokens"))


def _meter(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def required_provider_operation(task: dict[str, Any]) -> str:
    task_class = task["task_class"]
    if task_class in {"deep_crawl", "js_rendered_content"}:
        return "crawl"
    return "search"


def cell_applicability(
    task: dict[str, Any],
    mode: str,
    provider_id: str,
    harness_id: str,
    model_profile: str,
    operation: str,
    provider_capabilities: dict[str, frozenset[str]],
    pi_driver: PiHarnessDriver,
) -> tuple[bool, str | None]:
    if mode == "fixture" and not task["fixture_mode_allowed"]:
        return False, "fixture_not_allowed"
    if provider_id not in {"native", "no-search"} and operation not in provider_capabilities.get(
        provider_id, frozenset()
    ):
        return False, f"provider_lacks_{operation}"
    spec = harnesses.find(harness_id)
    if spec is not None and spec.live and provider_id == "native" and not spec.native_search:
        return False, "native_search_unavailable"
    if harness_id == "pi":
        profile = pi_driver.profile(model_profile)
        if provider_id == "native" and not profile.provider_adapter_exposure.get(
            "native_search_available", False
        ):
            return False, "native_search_unavailable"
        if provider_id in EXTERNAL_PROVIDERS and not profile.supports_external_tools:
            return False, "tool_calling_unsupported"
    return True, None


def max_attempts_for(suite: dict[str, Any], task: dict[str, Any]) -> int:
    raw = task.get("retries") or suite.get("retries") or {}
    attempts = raw.get("max_attempts", 0)
    return max(1, int(attempts) + 1)


def validate_suite_run_id(run_id: str) -> str:
    if (
        not run_id
        or run_id in {".", ".."}
        or Path(run_id).is_absolute()
        or Path(run_id).name != run_id
        or "/" in run_id
        or "\\" in run_id
    ):
        raise RunnerError(
            "run id must be a single path segment without directory separators or traversal"
        )
    return run_id


def attempt_cell(cell: MatrixCell, attempt: int) -> MatrixCell:
    if attempt <= 1:
        return cell
    return replace(cell, run_id=f"{cell.run_id}-att{attempt}")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def stable_run_id(
    suite_id: str,
    suite_version: str,
    task_id: str,
    provider_id: str,
    harness_id: str,
    model_profile: str,
    repetition: int,
) -> str:
    material = "|".join(
        (suite_id, suite_version, task_id, provider_id, harness_id, model_profile, str(repetition))
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"sew-{digest}"


def stable_suite_run_id(
    suite: dict[str, Any],
    *,
    seed: int | None,
    repetitions: int | None,
    mode: str = "fixture",
) -> str:
    material = "|".join(
        (
            suite["suite_id"],
            str(suite["version"]),
            str(seed if seed is not None else suite["randomization_seed"]),
            str(repetitions if repetitions is not None else suite["repetitions"]),
        )
    )
    # Fixture ids are unchanged; a live run gets its own id, so it never
    # resumes (or is refused by) the fixture run of the same suite and seed.
    if mode != "fixture":
        material += f"|{mode}"
        prefix = f"{suite['suite_id']}-{mode}"
    else:
        prefix = suite["suite_id"]
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:12]}"


def normalize_runner_status(status: str) -> str:
    if status == "unsupported":
        return "not_applicable"
    return status


def trailing_unavailable_streak(index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The index entries that end on an unfinished ``provider_unavailable`` streak.

    Mirrors ``run()``: cells that never executed (no ``elapsed_seconds``) and
    cells out of deferrals neither extend nor break a streak. The entries are
    the index's own dicts, so the runner can remove them on a streak stop.
    """

    streak: list[dict[str, Any]] = []
    for entry in reversed(index):
        if "elapsed_seconds" not in entry:
            continue
        if entry.get("status") != "provider_unavailable":
            break
        if entry.get("unavailable_deferrals", 0) < PROVIDER_UNAVAILABLE_MAX_DEFERRALS:
            streak.insert(0, entry)
    return streak


def summarize_index(
    index: list[dict[str, Any]],
    total_cells: int,
    stopped_reason: str | None,
    deferred_unavailable: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    run_ids = []
    exhausted = []
    for entry in index:
        status = str(entry.get("status"))
        counts[status] = counts.get(status, 0) + 1
        if entry.get("run_dir"):
            run_ids.append(str(entry["run_id"]))
        if (
            status == "provider_unavailable"
            and entry.get("unavailable_deferrals", 0) >= PROVIDER_UNAVAILABLE_MAX_DEFERRALS
        ):
            exhausted.append(str(entry.get("cell_key")))
    return {
        "total_cells": total_cells,
        "completed_cells": len(index),
        "remaining_cells": total_cells - len(index),
        "status_counts": counts,
        "duplicate_run_ids": sorted(run_id for run_id in set(run_ids) if run_ids.count(run_id) > 1),
        "stopped_reason": stopped_reason,
        # Of remaining_cells, how many a provider_unavailable streak deferred
        # (the rest never started).
        "deferred_unavailable_cells": len(deferred_unavailable or {}),
        # Cells recorded provider_unavailable because they ran out of
        # deferrals; a resume while the wall was still up can put cells here.
        "unavailable_exhausted_cells": sorted(exhausted),
    }


def cell_asdict(cell: MatrixCell) -> dict[str, Any]:
    data = asdict(cell)
    data["cell_key"] = cell.key
    return data


__all__ = [
    "CellBudgets",
    "CellExecution",
    "FixtureCellExecutor",
    "LiveCellExecutor",
    "MatrixCell",
    "OperatorBudgets",
    "RunnerError",
    "SuiteRunner",
    "adopt_completed_bundle",
    "cell_applicability",
    "live_process_meters",
    "load_provider_exposures",
    "max_attempts_for",
    "stable_run_id",
    "unavailable_bundles",
]
