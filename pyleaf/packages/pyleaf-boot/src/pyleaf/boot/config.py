"""Layered config loading for pyleaf-boot.

``load_environment`` builds a :class:`pyleaf.core.Environment` whose sources
are ordered lowest-to-highest precedence (later wins):

1. ``<config_dir>/application.yaml`` (if present)
2. ``<config_dir>/application-<profile>.yaml`` per active profile
   (missing files are skipped; malformed yaml raises ``PyleafError``)
3. environment variables (``env`` mapping, default ``os.environ``)
4. ``--key=value`` CLI arguments
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pyleaf.core import Environment, PropertySource, PyleafError

_CLI_PREFIX = "--"


def _flatten(node: Any, stem: str, out: dict[str, Any]) -> None:
    """Flatten nested mappings into ``a.b.c`` keys; leaves stay raw."""
    if isinstance(node, Mapping):
        for key, value in node.items():
            child = f"{stem}.{key}" if stem else str(key)
            _flatten(value, child, out)
    else:
        out[stem] = node


def _yaml_source(path: Path) -> PropertySource | None:
    """Load ``path`` as a flattened property source, or None when absent."""
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PyleafError(f"Malformed YAML in config file '{path}': {exc}") from exc
    if data is not None and not isinstance(data, Mapping):
        raise PyleafError(
            f"Malformed YAML in config file '{path}':"
            " top-level document must be a mapping"
        )
    flat: dict[str, Any] = {}
    if data is not None:
        _flatten(data, "", flat)
    return PropertySource(name=f"yaml:{path.name}", values=flat)


def _env_key(var: str) -> str:
    """Map an env var name to a property key.

    Lowercased, ``_`` becomes ``.`` and dot runs collapse — so both
    ``APP_DB_URL`` and ``APP__DB__URL`` yield ``app.db.url``.
    """
    parts = [part for part in var.lower().replace("_", ".").split(".") if part]
    return ".".join(parts)


def _parse_cli(cli_args: Sequence[str]) -> dict[str, str]:
    """Parse ``--key=value`` arguments; bare ``--flags`` are an error."""
    values: dict[str, str] = {}
    for arg in cli_args:
        if not arg.startswith(_CLI_PREFIX):
            continue
        body = arg[len(_CLI_PREFIX) :]
        key, sep, value = body.partition("=")
        if not sep or not key:
            raise PyleafError(
                f"Invalid CLI argument '{arg}': expected --key=value form"
            )
        values[key] = value
    return values


def load_environment(
    config_dir: str | Path = ".",
    profiles: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    cli_args: Sequence[str] = (),
) -> Environment:
    """Build a layered :class:`Environment` from yaml files, env vars and CLI.

    Later property sources win: CLI args beat env vars, which beat
    ``application-<profile>.yaml``, which beats ``application.yaml``.
    Missing yaml files are skipped; malformed yaml raises ``PyleafError``
    naming the file. ``env`` defaults to ``os.environ``; every variable is
    mapped (lowercased, ``_`` -> ``.``).
    """
    base = Path(config_dir)
    sources: list[PropertySource] = []

    main = _yaml_source(base / "application.yaml")
    if main is not None:
        sources.append(main)
    for profile in profiles:
        profile_source = _yaml_source(base / f"application-{profile}.yaml")
        if profile_source is not None:
            sources.append(profile_source)

    environ = os.environ if env is None else env
    sources.append(
        PropertySource(
            name="env",
            values={_env_key(var): value for var, value in environ.items()},
        )
    )

    cli = _parse_cli(cli_args)
    if cli:
        sources.append(PropertySource(name="cli", values=cli))

    return Environment(sources=sources, active_profiles=profiles)
