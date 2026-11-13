from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cangjie_build import git as git_mod
from cangjie_build.errors import BuildError


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin",
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
        },
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> tuple[str, dict[str, str]]:
    """A bare repository on a branch, a tag and an off-branch commit."""
    bare = tmp_path / "origin.git"
    work = tmp_path / "seed"
    _git("init", "--quiet", "--bare", str(bare), cwd=tmp_path)
    _git("init", "--quiet", str(work), cwd=tmp_path)
    _git("remote", "add", "origin", f"file://{bare}", cwd=work)
    for index in range(6):
        (work / f"file{index}.txt").write_text(f"{index}\n")
        _git("add", ".", cwd=work)
        _git("commit", "--quiet", "-m", f"commit {index}", cwd=work)
    _git("branch", "--move", "main", cwd=work)
    _git("tag", "v1.0", "HEAD~3", cwd=work)
    _git("push", "--quiet", "origin", "main", "--tags", cwd=work)
    commits = {
        f"HEAD~{offset}": _git("rev-parse", f"HEAD~{offset}", cwd=work) for offset in range(6)
    }
    return f"file://{bare}", commits


def test_a_shortened_hash_is_resolved_and_checked_out(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    """`git clone --branch <prefix>` asks the server for a ref named after the prefix.

    The server answers "remote branch not found", which reads as a wrong hash
    even though the hash is right. Only a full hash is a valid want, so a
    shortened one has to be resolved to its full form first.
    """
    url, commits = origin
    short = commits["HEAD~2"][:12]

    git_mod.shallow_clone(url, tmp_path / "checkout", tag=short)

    assert git_mod.head_commit(tmp_path / "checkout", stage="t") == commits["HEAD~2"]
    assert (tmp_path / "checkout" / "file3.txt").exists()


def test_an_uppercase_hash_is_the_same_commit(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    url, commits = origin

    git_mod.shallow_clone(url, tmp_path / "checkout", tag=commits["HEAD~1"].upper())

    assert git_mod.head_commit(tmp_path / "checkout", stage="t") == commits["HEAD~1"]


def test_a_branch_and_a_tag_both_check_out(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    url, commits = origin

    git_mod.shallow_clone(url, tmp_path / "branch", tag="main")
    git_mod.shallow_clone(url, tmp_path / "tag", tag="v1.0")

    assert git_mod.head_commit(tmp_path / "branch", stage="t") == commits["HEAD~0"]
    assert git_mod.head_commit(tmp_path / "tag", stage="t") == commits["HEAD~3"]


def test_a_hash_that_names_no_commit_is_reported_with_the_reason(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    url, _ = origin

    with pytest.raises(BuildError, match="does not name exactly one commit"):
        git_mod.shallow_clone(url, tmp_path / "checkout", tag="1234567")

    assert not (tmp_path / "checkout").exists()


def test_resolution_leaves_no_scratch_repository_behind(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    """The resolution clone is a full fetch of the refs; it must not accumulate."""
    url, commits = origin
    root = tmp_path / "workspace"
    root.mkdir()

    git_mod.shallow_clone(url, root / "checkout", tag=commits["HEAD~2"][:10], scratch_root=root)

    assert sorted(p.name for p in root.iterdir()) == ["checkout"]


def test_an_existing_checkout_at_another_commit_is_refused(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    """Reusing it would build and label the old revision with the new version."""
    url, commits = origin
    dest = tmp_path / "checkout"
    git_mod.shallow_clone(url, dest, tag=commits["HEAD~3"])
    git_mod.require_checkout(dest, tag=commits["HEAD~3"], stage="t")

    with pytest.raises(BuildError, match=f"was cloned at {commits['HEAD~3']}"):
        git_mod.require_checkout(dest, tag=commits["HEAD~1"], stage="t")


def test_an_existing_checkout_of_another_branch_is_refused(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    """A ref the checkout still holds is not enough: HEAD is what gets built."""
    url, commits = origin
    dest = tmp_path / "checkout"
    git_mod.shallow_clone(url, dest, tag="main")
    # Give the repository a tag pointing somewhere else while HEAD stays on main:
    # a ref that resolves locally is not evidence that it is what was checked out.
    _git(
        "fetch",
        "--quiet",
        "--depth",
        "1",
        "origin",
        "refs/tags/v1.0:refs/tags/v1.0",
        cwd=dest,
    )

    with pytest.raises(BuildError, match="checked out at"):
        git_mod.require_checkout(dest, tag="v1.0", stage="t")
    assert commits["HEAD~0"] == git_mod.head_commit(dest, stage="t")


def test_an_existing_checkout_can_be_reused_by_its_own_short_hash(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    url, commits = origin
    dest = tmp_path / "checkout"
    git_mod.shallow_clone(url, dest, tag=commits["HEAD~2"][:12])

    git_mod.require_checkout(dest, tag=commits["HEAD~2"], stage="t")
    git_mod.require_checkout(dest, tag=commits["HEAD~2"][:9], stage="t")


def test_a_branch_named_like_a_hash_is_taken_as_the_branch(tmp_path: Path) -> None:
    """`deadbeef` may be a branch, and a branch name wins over a hash prefix."""
    bare, work = tmp_path / "origin.git", tmp_path / "seed"
    env = {
        "PATH": "/usr/bin:/bin",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
    }

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    git("init", "--quiet", "--bare", str(bare), cwd=tmp_path)
    git("init", "--quiet", str(work), cwd=tmp_path)
    git("remote", "add", "origin", f"file://{bare}", cwd=work)
    (work / "a.txt").write_text("a\n")
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", "on main", cwd=work)
    git("branch", "--move", "main", cwd=work)
    git("checkout", "--quiet", "-b", "deadbeef", cwd=work)
    (work / "b.txt").write_text("b\n")
    git("add", ".", cwd=work)
    git("commit", "--quiet", "-m", "on the hex-named branch", cwd=work)
    git("push", "--quiet", "origin", "main", "deadbeef", cwd=work)
    branch_head = git("rev-parse", "deadbeef", cwd=work)
    assert branch_head != git("rev-parse", "main", cwd=work)

    dest = tmp_path / "checkout"
    git_mod.shallow_clone(f"file://{bare}", dest, tag="deadbeef")

    assert git_mod.head_commit(dest, stage="t") == branch_head
    assert (dest / "b.txt").exists()
    # The branch is named by a ref, so reusing the checkout can confirm it.
    git_mod.require_checkout(dest, tag="deadbeef", stage="t")


def test_a_ref_that_git_would_read_as_an_option_is_refused(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    url, _ = origin

    with pytest.raises(BuildError, match="reads as an option"):
        git_mod.shallow_clone(url, tmp_path / "checkout", tag="--upload-pack=evil")


def test_a_ref_containing_whitespace_is_refused(
    origin: tuple[str, dict[str, str]], tmp_path: Path
) -> None:
    url, _ = origin

    with pytest.raises(BuildError, match="whitespace"):
        git_mod.shallow_clone(url, tmp_path / "checkout", tag="main extra")
