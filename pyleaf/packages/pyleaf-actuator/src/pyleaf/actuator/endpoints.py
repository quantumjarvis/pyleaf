"""Payload builders for the actuator endpoints.

Each function takes the :class:`ApplicationContext` (and the environment
it carries) and returns JSON-able data; the FastAPI route handlers in
``pyleaf.actuator.autoconfigure`` are thin wrappers around them.

``/beans`` enumerates *definitions* only — it reads ``dep_specs`` and
registry candidates without ever calling a factory, so lazy singletons
stay uninstantiated.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyleaf.boot.properties import collect_prefixed
from pyleaf.core.resolver import DepKind
from pyleaf.core.stereotypes import CONFIG_PROPS_ATTR

from .health import HealthIndicator

if TYPE_CHECKING:
    from pyleaf.core.context import ApplicationContext
    from pyleaf.core.definition import BeanDefinition

MASKED = "******"

# Keys whose name suggests sensitive content (matches Spring's defaults).
_SECRET_KEY_PATTERN = re.compile(r"password|secret|token|key", re.IGNORECASE)


def _candidates(ctx: ApplicationContext, required: Any) -> list[BeanDefinition]:
    """Definitions assignable to ``required`` — BeanRegistry.candidates rules.

    Works off the public ``ctx.bean_definitions()``; internal plumbing
    defs only match their exact declared type.
    """
    found: list[BeanDefinition] = []
    for defn in ctx.bean_definitions():
        try:
            if not issubclass(defn.bean_type, required):
                continue
        except TypeError:
            # bean_type or required is not a class (e.g. generic alias).
            continue
        if defn.internal and defn.bean_type is not required:
            continue
        found.append(defn)
    return found


def _qualified_name(bean_type: Any) -> str:
    module = getattr(bean_type, "__module__", None)
    qualname = getattr(bean_type, "__qualname__", None) or getattr(
        bean_type, "__name__", None
    )
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    return str(bean_type)


def health_payload(ctx: ApplicationContext) -> tuple[int, dict[str, Any]]:
    """Aggregate every ``HealthIndicator`` bean's ``health()`` report."""
    components: dict[str, Any] = {}
    down = False
    for defn in _candidates(ctx, HealthIndicator):
        try:
            report = dict(ctx.get_bean(defn.name).health())
        except Exception:
            report = {"status": "DOWN", "error": "health() raised"}
        components[defn.name] = report
        if str(report.get("status", "")).upper() == "DOWN":
            down = True
    body = {"status": "DOWN" if down else "UP", "components": components}
    return (503 if down else 200), body


def info_payload(ctx: ApplicationContext) -> dict[str, Any]:
    """Merge ``info.*`` properties (under ``app``) plus best-effort git."""
    body = collect_prefixed(ctx.environment, "info")
    body.setdefault("app", {})
    git = _git_info()
    if git:
        body["git"] = git
    return body


def _secret_flagged(ctx: ApplicationContext) -> list[tuple[str, frozenset[str]]]:
    """``(prefix, secret_fields)`` of every @ConfigurationProperties def."""
    flagged: list[tuple[str, frozenset[str]]] = []
    for defn in ctx.bean_definitions():
        bean_type = defn.bean_type
        meta = (
            getattr(bean_type, CONFIG_PROPS_ATTR, None)
            if isinstance(bean_type, type)
            else None
        )
        if meta is not None:
            flagged.append((meta.prefix, meta.secret_fields))
    return flagged


def _is_masked(key: str, flagged: list[tuple[str, frozenset[str]]]) -> bool:
    if _SECRET_KEY_PATTERN.search(key):
        return True
    leaf = key.rpartition(".")[2]
    return any(
        key.startswith(f"{prefix}.") and leaf in fields
        for prefix, fields in flagged
    )


def env_payload(ctx: ApplicationContext) -> list[dict[str, Any]]:
    """Per-source property maps with secret values replaced by ``******``."""
    flagged = _secret_flagged(ctx)
    sources: list[dict[str, Any]] = []
    for source in ctx.environment.sources:
        properties = {
            key: (MASKED if _is_masked(key, flagged) else value)
            for key, value in source.values.items()
        }
        sources.append({"name": source.name, "properties": properties})
    return sources


def _dep_names(ctx: ApplicationContext, defn: BeanDefinition) -> list[str]:
    """Bean names this def's ``dep_specs`` would resolve to (no creation)."""
    names: list[str] = []
    try:
        specs = defn.dep_specs()
    except Exception:
        return names
    for spec in specs:
        cands = _candidates(ctx, spec.bean_type)
        if spec.qualifier is not None:
            cands = [c for c in cands if c.name == spec.qualifier]
        if spec.kind is DepKind.LIST:
            resolved = [c.name for c in cands]
        elif len(cands) == 1:
            resolved = [cands[0].name]
        else:
            primaries = [c for c in cands if c.primary]
            resolved = (
                [primaries[0].name]
                if len(primaries) == 1
                else [c.name for c in cands]
            )
        for name in resolved:
            if name not in names:
                names.append(name)
    return names


def beans_payload(ctx: ApplicationContext) -> list[dict[str, Any]]:
    """Every BeanDefinition: name, qualified type, scope, flags, deps."""
    return [
        {
            "name": defn.name,
            "type": _qualified_name(defn.bean_type),
            "scope": defn.scope.name.lower(),
            "primary": defn.primary,
            "lazy": defn.lazy,
            "dependencies": _dep_names(ctx, defn),
        }
        for defn in ctx.bean_definitions()
    ]


def conditions_payload(ctx: ApplicationContext) -> list[dict[str, Any]]:
    """Render ``ctx.condition_report`` — including out-of-band entries."""
    return [
        {
            "name": entry.bean_name,
            "matched": entry.matched,
            "reasons": list(entry.reasons),
        }
        for entry in ctx.condition_report.entries
    ]


def _find_gitdir(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.git`` dir or worktree file."""
    for directory in (start, *start.parents):
        candidate = directory / ".git"
        if candidate.is_dir():
            return candidate
        if candidate.is_file():
            # Worktree/submodule link: "gitdir: <path>".
            try:
                content = candidate.read_text().strip()
            except OSError:
                return None
            if not content.startswith("gitdir:"):
                return None
            path = Path(content.split(":", 1)[1].strip())
            return path if path.is_absolute() else (directory / path).resolve()
    return None


def _git_info() -> dict[str, Any]:
    """Best-effort ``{"commit": {"id": ...}, "branch": ...}``; {} on failure."""
    try:
        gitdir = _find_gitdir(Path.cwd())
        if gitdir is None:
            return {}
        head = (gitdir / "HEAD").read_text().strip()
        branch = ""
        commit = ""
        if head.startswith("ref:"):
            ref = head.split(":", 1)[1].strip()
            branch = ref.rpartition("/")[2]
            ref_file = gitdir / ref
            if ref_file.is_file():
                commit = ref_file.read_text().strip()
            else:
                packed = gitdir / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text().splitlines():
                        line = line.strip()
                        if line and not line.startswith("#") and line.endswith(ref):
                            commit = line.split(" ", 1)[0]
                            break
        else:
            # Detached HEAD: the file holds the commit id directly.
            commit = head
        if not commit:
            return {}
        info: dict[str, Any] = {"commit": {"id": commit}}
        if branch:
            info["branch"] = branch
        return info
    except Exception:
        return {}


__all__ = [
    "beans_payload",
    "conditions_payload",
    "env_payload",
    "health_payload",
    "info_payload",
]
