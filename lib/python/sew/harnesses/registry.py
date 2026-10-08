"""The harness registry: one entry per harness id (OHM-01).

Every per-harness decision the bench makes outside the judges reads one
:class:`HarnessSpec`: the schema's closed harness set, the runner's live
harnesses, the live driver's binary and protocol, the CLI choices, the arm
spawn writer and its forbidden flags, the doctor's binary checks and the site's
harness label. Adding a harness is a module that defines its spec plus one
registry line in :mod:`sew.harnesses`.

The module is pure: it imports nothing from ``sew``. Entries name their
protocol, spawn writer and usage parser as ``"module:attr"`` references that
resolve on first use, so the registry can sit below ``sew.schema`` without an
import cycle. A test may pass the objects themselves instead.

The derived collections (:func:`ids`, :func:`field_map`, :func:`protocols`)
are live views, not snapshots: a harness registered after import is visible to
every module that holds one.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Iterator, Mapping, Set
from dataclasses import dataclass
from typing import Any

# A reference to a callable or class: "package.module:attribute", or the object.
Ref = str | Callable[..., Any]
HARNESS_ID_RE = re.compile(r"[a-z][a-z0-9-]*")
# How a harness's GAP code cell is sandboxed: Claude Code's pinned sandbox
# runtime, Codex's own `codex sandbox`, or none (no code-cell path).
CODE_CELL_SANDBOXES = frozenset({"srt", "codex-sandbox"})


@dataclass(frozen=True)
class HarnessSpec:
    """Everything the bench knows about one harness id."""

    id: str
    label: str
    # Spawned for real by sew.live_harness. Only live harnesses have a binary,
    # a protocol and an arm spawn writer, and only they are CLI --harness choices.
    live: bool = False
    bin_env: str | None = None
    default_bin: str | None = None
    # A sew.live_harness.HarnessProtocol subclass.
    protocol: Ref | None = None
    # writer(config, contract, server_config, scratch, source_env, *, harness_auth)
    # -> sew.arms.SpawnSurface: the per-cell MCP config and tool allowlist.
    arm_spawn: Ref | None = None
    # Harness arguments the arm owns; a caller may not pass them.
    forbidden_flags: frozenset[str] = frozenset()
    # The harness has its own web search, so it can run the native arm.
    native_search: bool = False
    code_cell_sandbox: str | None = None
    # The function that turns the harness's reported usage into SEW token rows.
    usage_parser: Ref | None = None
    # Which price source rates the harness's model tokens.
    pricing_key: str | None = None
    # Minimum supported CLI version, checked by doctor without a model call.
    min_version: tuple[int, ...] | None = None
    # An open-source harness (Hermes, Pi, Opencode), switched by the OSS flag.
    oss: bool = False

    def __post_init__(self) -> None:
        if not HARNESS_ID_RE.fullmatch(self.id):
            raise ValueError(f"harness id must match {HARNESS_ID_RE.pattern}: {self.id!r}")
        if not self.label:
            raise ValueError(f"harness {self.id!r} needs a display label")
        if self.live:
            missing = [
                name
                for name in ("bin_env", "default_bin", "protocol", "arm_spawn")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"live harness {self.id!r} is missing {', '.join(missing)}")
        if self.code_cell_sandbox is not None and self.code_cell_sandbox not in CODE_CELL_SANDBOXES:
            raise ValueError(
                f"harness {self.id!r} code_cell_sandbox must be one of "
                f"{sorted(CODE_CELL_SANDBOXES)}, got {self.code_cell_sandbox!r}"
            )

    def load(self, name: str) -> Any:
        """Resolve one reference field (``protocol``, ``arm_spawn``, ``usage_parser``)."""

        ref = getattr(self, name)
        if ref is None:
            raise LookupError(f"harness {self.id!r} has no {name}")
        if not isinstance(ref, str):
            return ref
        module, _, attribute = ref.partition(":")
        return getattr(importlib.import_module(module), attribute)


_REGISTRY: dict[str, HarnessSpec] = {}
_PROTOCOL_CACHE: dict[str, tuple[HarnessSpec, Any]] = {}


def register(spec: HarnessSpec) -> HarnessSpec:
    if spec.id in _REGISTRY:
        raise ValueError(f"harness {spec.id!r} is already registered")
    _REGISTRY[spec.id] = spec
    return spec


def unregister(harness_id: str) -> HarnessSpec:
    _PROTOCOL_CACHE.pop(harness_id, None)
    return _REGISTRY.pop(harness_id)


def find(harness_id: object) -> HarnessSpec | None:
    return _REGISTRY.get(harness_id) if isinstance(harness_id, str) else None


def get(harness_id: str) -> HarnessSpec:
    spec = find(harness_id)
    if spec is None:
        raise KeyError(harness_id)
    return spec


def specs(**where: Any) -> tuple[HarnessSpec, ...]:
    """Registered specs in registration order, filtered by field equality."""

    return tuple(spec for spec in _REGISTRY.values() if _matches(spec, where))


def _matches(spec: HarnessSpec, where: Mapping[str, Any]) -> bool:
    return all(getattr(spec, name) == value for name, value in where.items())


class HarnessIds(Set):
    """A live, read-only set of the registered ids matching a filter."""

    def __init__(self, where: Mapping[str, Any]) -> None:
        self._where = dict(where)

    @classmethod
    def _from_iterable(cls, iterable):
        # Set algebra (HARNESSES - {"fixture"}) yields a plain snapshot.
        return frozenset(iterable)

    def __contains__(self, harness_id: object) -> bool:
        spec = find(harness_id)
        return spec is not None and _matches(spec, self._where)

    def __iter__(self) -> Iterator[str]:
        return iter([spec.id for spec in specs(**self._where)])

    def __len__(self) -> int:
        return len(specs(**self._where))

    def __repr__(self) -> str:
        return f"HarnessIds({list(self)!r})"


class HarnessFieldMap(Mapping):
    """A live, read-only ``{harness_id: spec.<field>}`` over matching specs."""

    def __init__(self, field: str, where: Mapping[str, Any]) -> None:
        self._field = field
        self._where = dict(where)

    def __getitem__(self, harness_id: str) -> Any:
        spec = find(harness_id)
        if spec is None or not _matches(spec, self._where):
            raise KeyError(harness_id)
        return getattr(spec, self._field)

    def __iter__(self) -> Iterator[str]:
        return iter([spec.id for spec in specs(**self._where)])

    def __len__(self) -> int:
        return len(specs(**self._where))

    def __repr__(self) -> str:
        return f"HarnessFieldMap({self._field}, {dict(self)!r})"


class HarnessProtocols(Mapping):
    """A live ``{harness_id: protocol instance}`` over the live harnesses."""

    def __getitem__(self, harness_id: str) -> Any:
        spec = find(harness_id)
        if spec is None or not spec.live:
            raise KeyError(harness_id)
        cached = _PROTOCOL_CACHE.get(harness_id)
        if cached is None or cached[0] is not spec:
            cached = (spec, spec.load("protocol")())
            _PROTOCOL_CACHE[harness_id] = cached
        return cached[1]

    def __iter__(self) -> Iterator[str]:
        return iter([spec.id for spec in specs(live=True)])

    def __len__(self) -> int:
        return len(specs(live=True))


def ids(**where: Any) -> HarnessIds:
    return HarnessIds(where)


def field_map(field: str, **where: Any) -> HarnessFieldMap:
    return HarnessFieldMap(field, where)


def protocols() -> HarnessProtocols:
    return HarnessProtocols()
