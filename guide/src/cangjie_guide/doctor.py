from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from cangjie_guide import registry
from cangjie_guide.errors import GuideError
from cangjie_guide.gh import Gh, GhError

# Scopes gh needs for what this tool does: dispatch, read runs, download artifacts.
REQUIRED_SCOPES: tuple[str, ...] = ("repo", "workflow")


class Status(StrEnum):
    OK = "ok"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def _ok(name: str, detail: str) -> Check:
    return Check(name, Status.OK, detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name, Status.FAIL, detail)


def _unknown(name: str, detail: str) -> Check:
    return Check(name, Status.UNKNOWN, detail)


def local_checks(gh: Gh) -> list[Check]:
    checks: list[Check] = []
    if not Gh.available():
        return [_fail("gh installed", "gh is not on PATH")]

    checks.append(_ok("gh installed", "found on PATH"))
    try:
        auth: dict[str, Any] = gh.auth_state()
    except GhError as exc:
        return [*checks, _fail("gh authenticated", str(exc))]

    state = str(auth.get("state", ""))
    login = str(auth.get("login", ""))
    if state == "success":
        checks.append(_ok("gh authenticated", f"as {login}"))
    else:
        checks.append(_fail("gh authenticated", f"state={state}"))

    scopes_raw = str(auth.get("scopes", ""))
    scopes = {s.strip() for s in scopes_raw.split(",") if s.strip()}
    missing = [s for s in REQUIRED_SCOPES if s not in scopes]
    if missing:
        checks.append(_fail("token scopes", f"missing {', '.join(missing)}; have {scopes_raw}"))
    else:
        checks.append(_ok("token scopes", scopes_raw))

    try:
        repo = gh.current_repo()
    except GhError as exc:
        checks.append(_fail("repository", str(exc)))
    else:
        checks.append(_ok("repository", repo))
    return checks


def repo_checks(gh: Gh, repo: str, ref: str) -> list[Check]:
    checks: list[Check] = []
    try:
        perms = gh.repo_permissions(repo)
    except GhError as exc:
        return [_fail("repository reachable", str(exc))]

    checks.append(_ok("repository reachable", repo))
    write = bool(perms.get("push") or perms.get("admin") or perms.get("maintain"))
    checks.append(
        _ok("write access", "dispatch allowed")
        if write
        else _fail("write access", "dispatching needs push, maintain or admin")
    )

    try:
        remote = set(gh.remote_workflow_files(repo, ref))
    except GhError as exc:
        checks.append(_fail("workflow files", str(exc)))
        remote: set[str] = set()

    for path in registry.DISPATCHABLE:
        name = path.rsplit("/", 1)[-1]
        checks.append(
            _ok(f"workflow {name}", f"present at {ref}")
            if name in remote
            else _fail(f"workflow {name}", f"missing at {ref}")
        )

    # Secret names are only readable with admin; a token without it gets a 403.
    # That is a limitation of the check, not a missing secret, so it is reported
    # as unknown rather than failed.
    try:
        present = set(gh.repo_secret_names(repo))
    except GhError as exc:
        checks.append(
            _unknown(
                "secrets",
                f"cannot list secrets: {exc.stderr.splitlines()[0] if exc.stderr else exc}",
            )
        )
        return checks

    for path in registry.DISPATCHABLE:
        needed = registry.WORKFLOW_SECRETS.get(path, ())
        if not needed:
            continue
        missing = [s for s in needed if s not in present]
        name = path.rsplit("/", 1)[-1]
        checks.append(
            _ok(f"secrets for {name}", f"{len(needed)} present")
            if not missing
            else _fail(f"secrets for {name}", f"missing {', '.join(missing)}")
        )
    return checks


def run(gh: Gh, *, repo: str | None, ref: str) -> list[Check]:
    checks = local_checks(gh)
    if repo is None:
        return checks
    try:
        registry.load(gh, repo, registry.BUILD_AZURE, ref)
    except (GhError, GuideError) as exc:
        checks.append(_fail(f"workflow schema at {ref}", str(exc)))
    return [*checks, *repo_checks(gh, repo, ref)]
