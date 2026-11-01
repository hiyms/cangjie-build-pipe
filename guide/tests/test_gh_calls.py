from __future__ import annotations

import pytest

from cangjie_guide import runs
from cangjie_guide.gh import Gh
from cangjie_guide.schema import Workflow


class _RecordingGh(Gh):
    """Records argv and answers with canned JSON."""

    def __init__(self, responses: dict[str, object] | None = None) -> None:
        super().__init__("gh")
        self.calls: list[list[str]] = []
        self.responses = responses or {}

    def _run(self, args: list[str]) -> str:
        self.calls.append(args)
        for key, payload in self.responses.items():
            if key in " ".join(args):
                import json

                return json.dumps(payload)
        return ""

    def text(self, args: list[str]) -> str:
        self.calls.append(args)
        return "https://github.com/owner/repo/actions/runs/42"


def _workflow() -> Workflow:
    return Workflow(
        file=".github/workflows/build-cangjie-azure.yml",
        display_name="Build",
        ref="master",
    )


def test_dispatch_names_the_repository() -> None:
    """Without -R, gh dispatches to the checkout's repository, not --repo."""
    gh = _RecordingGh()

    runs.dispatch(gh, _workflow(), {"tag": "main"}, repo="owner/repo", ref="master")

    argv = gh.calls[0]
    assert "-R" in argv
    assert argv[argv.index("-R") + 1] == "owner/repo"


def test_run_view_names_the_repository() -> None:
    gh = _RecordingGh({"run view": {"status": "completed"}})

    gh.run_view("owner/repo", "42", "status")

    argv = gh.calls[0]
    assert argv[argv.index("-R") + 1] == "owner/repo"


def test_cancel_names_the_repository() -> None:
    gh = _RecordingGh()

    runs.cancel(gh, "owner/repo", "42")

    argv = gh.calls[0]
    assert argv[:4] == ["run", "cancel", "42", "-R"]
    assert argv[4] == "owner/repo"


def test_active_runs_queries_the_named_repository() -> None:
    gh = _RecordingGh({"run list": [{"status": "in_progress", "databaseId": 7}]})

    active = runs.active_runs(gh, "owner/repo", ".github/workflows/build.yml")

    assert [entry["databaseId"] for entry in active] == [7]
    assert gh.calls[0][gh.calls[0].index("-R") + 1] == "owner/repo"


def test_artifact_lookup_reads_every_page() -> None:
    """One page is not the whole list once a repository has many test reports."""
    first = {"artifacts": [{"name": "cangjie-linux-x64-1.0.0", "id": 1}]}
    second = {"artifacts": [{"name": "test-llt-linux-x64-1.log", "id": 2}]}
    gh = _RecordingGh({"actions/artifacts": [first, second]})

    found = gh.repo_artifacts("owner/repo")

    assert [entry["id"] for entry in found] == [1, 2]
    argv = gh.calls[0]
    assert "--paginate" in argv
    assert "--slurp" in argv


def test_an_invalid_artifact_page_is_an_error() -> None:
    """A paginated body that is not a list of pages must not read as "no artifacts"."""
    gh = _RecordingGh({"actions/artifacts": {"artifacts": []}})

    with pytest.raises(Exception, match="exit"):
        Gh.paged_artifacts(gh, "repos/owner/repo/actions/artifacts")
