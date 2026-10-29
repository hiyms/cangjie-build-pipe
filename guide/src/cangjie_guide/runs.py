from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from cangjie_guide.errors import GuideError
from cangjie_guide.gh import Gh, GhError, GhErrorKind
from cangjie_guide.schema import Artifact, Workflow

_RUN_URL_RE = re.compile(r"/actions/runs/(\d+)\s*$")

# Poll cadence: cheap early (the provision job answers in seconds, and a bad
# dispatch should be visible immediately), then backing off, because a build runs
# for tens of minutes and every poll costs two HTTP calls.
POLL_STEPS: tuple[float, ...] = (5, 5, 10, 10, 20, 30, 30, 60)


def dispatch(gh: Gh, workflow: Workflow, inputs: dict[str, str], *, ref: str) -> str:
    """Dispatch and return the run id.

    gh prints the run URL, which is the only race-free way to learn the id: the
    run list is ordered by a one-second-resolution timestamp, so two dispatches in
    the same second are indistinguishable there.
    """
    args = ["workflow", "run", workflow.file, "--ref", ref]
    for key, value in inputs.items():
        args += ["-f", f"{key}={value}"]
    out = gh.text(args).strip()
    match = _RUN_URL_RE.search(out)
    if match is None:
        raise GuideError(
            f"could not read a run id from `gh workflow run` output: {out!r}. "
            "This gh version may predate run-detail reporting."
        )
    return match.group(1)


def wait(
    gh: Gh, run_id: str, *, on_poll: Callable[[dict[str, Any]], None] | None = None
) -> dict[str, Any]:
    """Poll until the run completes, tolerating transient failures.

    A failure mid-watch is retried, because a 40-minute build will outlive a
    gateway blip. Anything classified fatal is raised instead: waiting cannot
    fix a repository that does not exist.
    """
    attempt = 0
    while True:
        try:
            payload = gh.run_view(
                run_id, "status,conclusion,jobs,databaseId,displayTitle,url,createdAt"
            )
        except GhError as exc:
            if exc.kind is not GhErrorKind.RETRYABLE:
                raise
            attempt += 1
            if attempt > 5:
                raise
            time.sleep(POLL_STEPS[min(attempt, len(POLL_STEPS) - 1)])
            continue
        attempt = 0
        if on_poll is not None:
            on_poll(payload)
        if payload.get("status") == "completed":
            return payload
        time.sleep(POLL_STEPS[min(1, len(POLL_STEPS) - 1)])


def cancel(gh: Gh, run_id: str) -> None:
    gh.text(["run", "cancel", run_id])


def artifacts_of(gh: Gh, run_id: str) -> list[Artifact]:
    return [Artifact.from_api(raw) for raw in gh.artifacts(run_id)]


def find_sdk_artifact(artifacts: list[Artifact], target: str) -> Artifact | None:
    """The SDK artifact for one target, ignoring test reports and other targets.

    A matrix build uploads both linux and windows artifacts under the same
    version, so taking the first match would pick the one that cannot run on a
    linux test VM.
    """
    prefix = f"cangjie-{target}-"
    for art in artifacts:
        if art.name.startswith(prefix) and not art.name.startswith("cangjie-test-reports-"):
            return art
    return None


def download(gh: Gh, run_id: str, name: str, dest: str) -> None:
    gh.text(["run", "download", run_id, "-n", name, "-D", dest])


def sdk_archive_name(target: str, version: str) -> str:
    """The archive basename the packaging stage writes.

    Not the artifact name: the artifact wraps cangjie-sdk-<target>-<version>.tar.gz
    and cangjie-stdx-<target>-<version>.<n>.tar.gz, while the artifact itself is
    named cangjie-<target>-<version>.
    """
    return f"cangjie-sdk-{target}-{version}"


def active_runs(gh: Gh, workflow_file: str, *, status_filter: str = "") -> list[dict[str, Any]]:
    """Runs of one workflow that are queued or running.

    This repository declares no `concurrency:`, so a second dispatch is not
    queued behind the first: it provisions its own VM. Two 16-vCPU Spot VMs
    compete for the same lowPriorityCores quota, and the second one can escalate
    to Regular at roughly five times the price without anyone asking for it.
    """
    args = [
        "run",
        "list",
        "--workflow",
        workflow_file,
        "--limit",
        "20",
        "--json",
        "databaseId,status,conclusion,headBranch,createdAt",
    ]
    runs: list[dict[str, Any]] = gh.json(args)
    active = [
        r
        for r in runs
        if r.get("status") in {"queued", "in_progress", "requested", "waiting", "pending"}
    ]
    if status_filter:
        active = [r for r in active if str(r.get("headBranch", "")) == status_filter]
    return active


def conflicting_artifacts(gh: Gh, repo: str, artifact_name: str) -> list[Artifact]:
    """Unexpired artifacts that a new build of the same version would overwrite.

    upload-artifact replaces an artifact of the same name, so a rebuild silently
    takes away the archive a running test may have been pointed at.
    """
    return [
        art
        for art in (Artifact.from_api(raw) for raw in gh.repo_artifacts(repo))
        if art.name == artifact_name and not art.expired
    ]
