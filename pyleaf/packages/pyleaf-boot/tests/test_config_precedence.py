"""Layered property-source precedence for ``load_environment``."""

from pathlib import Path

import pytest
from pyleaf.boot import load_environment
from pyleaf.core import PyleafError


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_cli_beats_env_beats_profile_yaml_beats_base_yaml(tmp_path: Path) -> None:
    """Full stack: --b=5 wins over B=4 over dev yaml b=3 over base b=2."""
    _write(tmp_path / "application.yaml", "a: 1\nb: 2\n")
    _write(tmp_path / "application-dev.yaml", "b: 3\n")

    env = load_environment(
        config_dir=tmp_path,
        profiles=["dev"],
        env={"B": "4"},
        cli_args=["--b=5"],
    )

    assert env.get("b") == "5"
    assert env.get("b", int) == 5
    # ``a`` exists only in the base yaml and survives untouched.
    assert env.get("a") == 1
    assert env.active_profiles == frozenset({"dev"})


def test_env_beats_profile_yaml(tmp_path: Path) -> None:
    _write(tmp_path / "application.yaml", "a: 1\nb: 2\n")
    _write(tmp_path / "application-dev.yaml", "b: 3\n")

    env = load_environment(config_dir=tmp_path, profiles=["dev"], env={"B": "4"})

    assert env.get("b") == "4"


def test_profile_yaml_beats_base_yaml(tmp_path: Path) -> None:
    _write(tmp_path / "application.yaml", "a: 1\nb: 2\n")
    _write(tmp_path / "application-dev.yaml", "b: 3\n")

    env = load_environment(config_dir=tmp_path, profiles=["dev"], env={})

    assert env.get("b") == 3


def test_base_yaml_used_when_no_profile(tmp_path: Path) -> None:
    _write(tmp_path / "application.yaml", "a: 1\nb: 2\n")

    env = load_environment(config_dir=tmp_path, env={})

    assert env.get("a") == 1
    assert env.get("b") == 2


def test_missing_profile_file_is_fine(tmp_path: Path) -> None:
    """A profile with no ``application-<profile>.yaml`` is skipped."""
    _write(tmp_path / "application.yaml", "a: 1\n")

    env = load_environment(config_dir=tmp_path, profiles=["dev", "staging"], env={})

    assert env.get("a") == 1
    assert env.active_profiles == frozenset({"dev", "staging"})


def test_no_config_dir_files_at_all(tmp_path: Path) -> None:
    """An empty config dir is not an error — env/cli still apply."""
    env = load_environment(config_dir=tmp_path, env={"X": "1"}, cli_args=["--y=2"])

    assert env.get("x") == "1"
    assert env.get("y") == "2"


def test_cli_requires_key_equals_value(tmp_path: Path) -> None:
    """``--key=value`` is the only supported CLI form; bare flags error."""
    with pytest.raises(PyleafError):
        load_environment(config_dir=tmp_path, env={}, cli_args=["--flag"])

    # Non-``--`` arguments are ignored positionals, not errors.
    env = load_environment(config_dir=tmp_path, env={}, cli_args=["run", "--a=1"])
    assert env.get("a") == "1"


def test_env_var_mapping_single_underscore(tmp_path: Path) -> None:
    """``APP_DB_URL`` lowercases and maps ``_`` to dots."""
    env = load_environment(config_dir=tmp_path, env={"APP_DB_URL": "postgres://x"})

    assert env.get("app.db.url") == "postgres://x"


def test_env_var_mapping_double_underscore(tmp_path: Path) -> None:
    """``APP__DB__URL`` maps to the same dotted key as single underscores."""
    env = load_environment(config_dir=tmp_path, env={"APP__DB__URL": "postgres://y"})

    assert env.get("app.db.url") == "postgres://y"


def test_nested_yaml_flattens_to_dotted_keys(tmp_path: Path) -> None:
    _write(tmp_path / "application.yaml", "app:\n  db:\n    url: postgres://z\n")

    env = load_environment(config_dir=tmp_path, env={})

    assert env.get("app.db.url") == "postgres://z"
