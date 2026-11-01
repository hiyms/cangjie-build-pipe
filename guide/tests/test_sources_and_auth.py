from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from cangjie_guide import runs
from cangjie_guide.errors import GuideError
from cangjie_guide.gh import Gh
from cangjie_guide.inputs import check_source

ROOT = Path(__file__).resolve().parents[2]


def _script() -> Path:
    return ROOT / ".github" / "scripts" / "build-fetch-args.sh"


def _run_script(value: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_script())],
        env={"COMPILER_SOURCE": value, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "value",
    [
        "git@host:owner/repo.git",
        "git@host:owner/repo.git#main",
    ],
)
def test_scp_syntax_is_refused_here_and_by_the_workflow_script(value: str) -> None:
    """A value this check accepted but the script refused would fail after the VM booted."""
    with pytest.raises(GuideError, match="scp syntax"):
        check_source(value)

    result = _run_script(value)
    assert result.returncode != 0
    assert "scp syntax" in result.stderr


@pytest.mark.parametrize(
    "value",
    [
        "https://github.com/CangjieLanguage/cangjie_compiler.git",
        "https://github.com/CangjieLanguage/cangjie_compiler.git#feat/windows-cross",
        "https://host:443/owner/repo.git",
        "atomgit.com/owner/repo.git#v1.0.2",
        "ssh://git@host/owner/repo.git",
    ],
)
def test_a_usable_source_is_accepted_by_both(value: str) -> None:
    assert check_source(value) == value

    result = _run_script(value)
    assert result.returncode == 0, result.stderr


def test_a_logged_out_gh_is_reported_rather_than_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gh auth status --json hosts` answers {"hosts": {}} when logged out."""
    config_dir = tmp_path / "gh-config"
    config_dir.mkdir()
    monkeypatch.setenv("GH_CONFIG_DIR", str(config_dir))

    with pytest.raises(GuideError, match="not logged in"):
        Gh("gh").auth_state()


class _FakeGh(Gh):
    """Answers run_view from a script of statuses, and never shells out."""

    def __init__(self, statuses: list[str]) -> None:
        super().__init__("gh")
        self._statuses = statuses

    def run_view(self, repo: str, run_id: str, fields: str) -> dict[str, Any]:
        del repo, run_id, fields
        status = self._statuses.pop(0)
        return {"status": status}


def test_wait_backs_off_as_polls_accumulate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long run must not keep polling every five seconds."""
    slept: list[float] = []
    monkeypatch.setattr(runs.time, "sleep", slept.append)
    gh = _FakeGh(["in_progress"] * 6 + ["completed"])

    runs.wait(gh, "owner/repo", "1")

    assert slept == [5, 10, 10, 20, 30, 30]
