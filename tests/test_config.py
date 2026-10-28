from __future__ import annotations

from pathlib import Path

import pytest

from cangjie_build.config import RepoName, RepoOverride, build_config, make_repos
from cangjie_build.errors import ConfigError


def test_default_repo_set_uses_github_org() -> None:
    repos = make_repos(global_tag=None)
    assert set(repos) == set(RepoName)
    for r in repos.values():
        assert r.url.startswith("https://github.com/CangjieLanguage/")
        assert r.tag is None


def test_global_tag_propagates_when_no_override() -> None:
    repos = make_repos(global_tag="v1.0.0")
    assert all(r.tag == "v1.0.0" for r in repos.values())


def test_empty_string_override_treated_as_unset() -> None:
    repos = make_repos(
        global_tag="v1.0.0",
        overrides={RepoName.COMPILER: RepoOverride(url="", tag="")},
    )
    assert repos[RepoName.COMPILER].url.startswith("https://github.com/CangjieLanguage")
    assert repos[RepoName.COMPILER].tag == "v1.0.0"


def test_per_repo_overrides_win_over_global_tag() -> None:
    overrides = {
        RepoName.COMPILER: RepoOverride(url="https://example.com/cc.git", tag="feature-x"),
        RepoName.RUNTIME: RepoOverride(tag="v2.0.0"),
    }
    repos = make_repos(global_tag="v1.0.0", overrides=overrides)

    assert repos[RepoName.COMPILER].url == "https://example.com/cc.git"
    assert repos[RepoName.COMPILER].tag == "feature-x"

    assert repos[RepoName.RUNTIME].url.startswith("https://github.com/CangjieLanguage")
    assert repos[RepoName.RUNTIME].tag == "v2.0.0"

    assert repos[RepoName.TOOLS].tag == "v1.0.0"
    assert repos[RepoName.STDX].tag == "v1.0.0"


def test_build_config_defaults(tmp_path: Path) -> None:
    cfg = build_config(workspace=tmp_path / "ws", build_root=tmp_path / "br")
    assert cfg.workspace == (tmp_path / "ws").resolve()
    assert cfg.build_root == (tmp_path / "br").resolve()
    assert cfg.target.spec.key == "linux-x64"
    assert cfg.build_type == "relwithdebinfo"
    assert cfg.cangjie_version == "0.0.0-dev"
    assert cfg.stdx_version == 1
    assert cfg.software_dir == cfg.workspace / "software"


def test_build_config_global_tag_used_as_version_when_unset(tmp_path: Path) -> None:
    cfg = build_config(workspace=tmp_path, build_root=tmp_path, global_tag="v9.9.9")
    # v-prefix is stripped so the runtime SemVer parser accepts it.
    assert cfg.cangjie_version == "9.9.9"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1.2.3", "1.2.3"),
        ("v1.2.3", "1.2.3"),
        ("V1.2.3", "1.2.3"),
        ("1.2.3-rc.1", "1.2.3-rc.1"),
        ("1.2.3+build.42", "1.2.3+build.42"),
        ("1.2.3-rc.1+build.42", "1.2.3-rc.1+build.42"),
        ("main", "0.0.0-dev"),
        ("feature/foo", "0.0.0-dev"),
        ("", "0.0.0-dev"),
        ("1.2", "0.0.0-dev"),
        ("1.2.3-", "0.0.0-dev"),
        ("1.2.3+", "0.0.0-dev"),
    ],
)
def test_build_config_normalises_cangjie_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
    expected: str,
) -> None:
    monkeypatch.delenv("CANGJIE_VERSION", raising=False)
    cfg = build_config(workspace=tmp_path, build_root=tmp_path, cangjie_version=raw)
    assert cfg.cangjie_version == expected


def test_build_config_rejects_invalid_build_type(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_config(workspace=tmp_path, build_root=tmp_path, build_type="lto")


def test_build_config_rejects_unknown_target(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        build_config(workspace=tmp_path, build_root=tmp_path, target_key="macos-arm64")


def test_repo_path_resolves(tmp_path: Path) -> None:
    cfg = build_config(workspace=tmp_path, build_root=tmp_path)
    assert cfg.repo_path("compiler") == tmp_path.resolve() / "cangjie_compiler"
    assert cfg.repo_path("stdx") == tmp_path.resolve() / "cangjie_stdx"
    with pytest.raises(ConfigError):
        cfg.repo_path("nope")


def test_shallow_clone_accepts_a_commit_sha(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PR head is only reachable by SHA, and `git clone --branch` rejects one."""
    from cangjie_build import git as git_mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, stage: str) -> None:
        calls.append(cmd)

    monkeypatch.setattr(git_mod, "run", fake_run)
    sha = "011f9c831eed6aa3d4d583f1f5d8ebcbb23a7e27"
    git_mod.shallow_clone("https://example.invalid/r.git", tmp_path / "r", tag=sha)

    flat = " ".join(" ".join(c) for c in calls)
    assert "clone --depth 1 --branch" not in flat
    assert "init --quiet" in flat
    assert f"fetch --depth 1 origin {sha}" in flat
    assert "checkout --quiet FETCH_HEAD" in flat


def test_shallow_clone_still_uses_branch_for_a_branch_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cangjie_build import git as git_mod

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, stage: str) -> None:
        calls.append(cmd)

    monkeypatch.setattr(git_mod, "run", fake_run)
    git_mod.shallow_clone("https://example.invalid/r.git", tmp_path / "r", tag="dev")

    assert calls == [
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            "dev",
            "https://example.invalid/r.git",
            str(tmp_path / "r"),
        ]
    ]
