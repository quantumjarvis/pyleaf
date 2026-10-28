"""Unit tests for the best-effort ``git`` block of ``/info``.

``_git_info`` reads ``.git`` directly (no git binary): a directory, or a
worktree/submodule ``gitdir: <path>`` link file. These tests build fake
``.git`` trees under ``tmp_path`` so every fallback branch — missing
directory, unreadable/invalid link file, loose ref, ``packed-refs``,
detached HEAD, missing commit, unreadable HEAD — is exercised.
"""

import os
from pathlib import Path

import pytest
from pyleaf.actuator.endpoints import _find_gitdir, _git_info, info_payload
from pyleaf.core import ApplicationContext, Environment, PropertySource

SHA = "a" * 40
OTHER_SHA = "b" * 40


def _make_gitdir(root: Path, head: str) -> Path:
    gitdir = root / ".git"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text(head)
    return gitdir


def _ctx() -> ApplicationContext:
    return ApplicationContext(
        environment=Environment(sources=[PropertySource("test", {})])
    )


def test_no_gitdir_anywhere_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # Precondition: none of tmp_path's ancestors may carry a .git.
    assert _find_gitdir(Path.cwd()) is None

    assert _git_info() == {}
    # /info merges nothing: just the (empty) app block.
    assert info_payload(_ctx()) == {"app": {}}


def test_loose_ref_resolves_commit_and_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gitdir = _make_gitdir(tmp_path, "ref: refs/heads/main")
    ref_file = gitdir / "refs" / "heads" / "main"
    ref_file.parent.mkdir(parents=True)
    ref_file.write_text(f"{SHA}\n")
    monkeypatch.chdir(tmp_path)

    assert _git_info() == {"commit": {"id": SHA}, "branch": "main"}


def test_packed_refs_resolves_commit_when_no_loose_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gitdir = _make_gitdir(tmp_path, "ref: refs/heads/main")
    (gitdir / "packed-refs").write_text(
        "# pack-refs with: peeled fully-peeled sorted\n"
        f"{OTHER_SHA} refs/heads/other\n"
        f"{SHA} refs/heads/main\n"
    )
    monkeypatch.chdir(tmp_path)

    assert _git_info() == {"commit": {"id": SHA}, "branch": "main"}


def test_unresolvable_ref_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gitdir = _make_gitdir(tmp_path, "ref: refs/heads/ghost")
    (gitdir / "packed-refs").write_text(f"{OTHER_SHA} refs/heads/other\n")
    monkeypatch.chdir(tmp_path)

    # No loose ref file and no packed-refs match -> commit stays "" -> {}.
    assert _git_info() == {}


def test_detached_head_has_commit_but_no_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_gitdir(tmp_path, SHA)
    monkeypatch.chdir(tmp_path)

    assert _git_info() == {"commit": {"id": SHA}}


def test_git_file_link_with_absolute_gitdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worktree/submodule layout: ``.git`` is a ``gitdir:`` link file."""
    real = tmp_path / "real.git"
    real.mkdir()
    (real / "HEAD").write_text(SHA)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {real}\n")
    monkeypatch.chdir(worktree)

    assert _git_info() == {"commit": {"id": SHA}}


def test_git_file_link_with_relative_gitdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real.git"
    real.mkdir()
    (real / "HEAD").write_text(SHA)

    worktree = tmp_path / "wt"
    worktree.mkdir()
    # Relative link: resolved against the directory holding the .git file.
    (worktree / ".git").write_text("gitdir: ../real.git\n")
    monkeypatch.chdir(worktree)

    assert _git_info() == {"commit": {"id": SHA}}


def test_git_file_without_gitdir_prefix_returns_none(tmp_path: Path) -> None:
    (tmp_path / ".git").write_text("not a link file\n")

    assert _find_gitdir(tmp_path) is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_git_file_returns_none(tmp_path: Path) -> None:
    dotgit = tmp_path / ".git"
    dotgit.write_text("gitdir: ../nowhere\n")
    dotgit.chmod(0)
    try:
        assert _find_gitdir(tmp_path) is None
    finally:
        dotgit.chmod(0o644)


def test_missing_head_is_swallowed_by_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()  # no HEAD inside
    monkeypatch.chdir(tmp_path)

    assert _git_info() == {}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_head_is_swallowed_by_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gitdir = _make_gitdir(tmp_path, SHA)
    head = gitdir / "HEAD"
    head.chmod(0)
    monkeypatch.chdir(tmp_path)
    try:
        assert _git_info() == {}
    finally:
        head.chmod(0o644)
