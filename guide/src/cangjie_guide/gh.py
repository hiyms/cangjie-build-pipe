from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from cangjie_guide.errors import GuideError


class GhErrorKind(StrEnum):
    """Why a gh invocation failed.

    Callers need to tell these apart: polling a run whose log does not exist yet
    is expected, a 5xx during a 40-minute watch is worth retrying, and a 404 on
    the repository never becomes true by waiting.
    """

    NOT_READY = "not-ready"
    RETRYABLE = "retryable"
    FATAL = "fatal"


class GhError(RuntimeError):
    def __init__(self, argv: list[str], returncode: int, stderr: str, kind: GhErrorKind) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr.strip()
        self.kind = kind
        super().__init__(f"{' '.join(argv)} exited {returncode}: {self.stderr}")


def _classify(stderr: str) -> GhErrorKind:
    lowered = stderr.lower()
    # Server-side conditions that clear on their own: a log Maple has not written
    # yet, an artifact not uploaded yet, throttling, or a gateway error.
    for marker in (
        "http 502",
        "http 503",
        "http 504",
        "rate limit",
        "timeout",
        "no valid artifacts",
    ):
        if marker in lowered:
            return GhErrorKind.RETRYABLE
    # "not found" for a log means the run has not reached that step yet.
    if "not found" in lowered and "log" in lowered:
        return GhErrorKind.RETRYABLE
    return GhErrorKind.FATAL


def _as_object(value: object, what: str) -> dict[str, Any]:
    """Assert a JSON object without narrowing it to an unreadable bare dict."""
    if not isinstance(value, dict):
        raise GuideError(f"{what}: expected a JSON object, got {type(value).__name__}")
    out: dict[str, Any] = {}
    for raw_key, item in cast(dict[Any, Any], value).items():
        out[str(raw_key)] = item
    return out


@dataclass(frozen=True)
class Gh:
    """Every gh call in this program goes through here.

    A single exit point is what makes the error classification trustworthy: if
    one call site decided for itself that a failure was fine, the "never fail
    silently" rule would hold only wherever someone remembered it.
    """

    executable: str = "gh"

    @staticmethod
    def available() -> bool:
        return shutil.which("gh") is not None

    def _run(self, args: list[str]) -> str:
        argv = [self.executable, *args]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
        if proc.returncode != 0:
            raise GhError(
                argv,
                proc.returncode,
                proc.stderr or proc.stdout,
                _classify(proc.stderr or proc.stdout),
            )
        return proc.stdout

    def json(self, args: list[str]) -> Any:
        out = self._run(args)
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise GhError([self.executable, *args], 0, out, GhErrorKind.FATAL) from exc

    def text(self, args: list[str]) -> str:
        return self._run(args)

    # -- facts the guide needs -------------------------------------------------

    def current_repo(self) -> str:
        """owner/name of the repository this directory belongs to."""
        return str(self.json(["repo", "view", "--json", "nameWithOwner"])["nameWithOwner"])

    def auth_state(self) -> dict[str, Any]:
        payload = self.json(["auth", "status", "--json", "hosts"])
        hosts = payload["hosts"]["github.com"]
        return dict(hosts[0])

    def repo_permissions(self, repo: str) -> dict[str, bool]:
        return dict(self.json(["api", f"repos/{repo}", "--jq", ".permissions"]))

    def repo_secret_names(self, repo: str) -> list[str]:
        payload = self.json(["api", f"repos/{repo}/actions/secrets?per_page=100"])
        return [str(s["name"]) for s in payload.get("secrets", [])]

    def remote_workflow_files(self, repo: str, ref: str) -> list[str]:
        payload = self.json(["api", f"repos/{repo}/contents/.github/workflows?ref={ref}"])
        return [str(entry["name"]) for entry in payload]

    def workflow_yaml(self, repo: str, path: str, ref: str) -> str:
        """The workflow file as committed at ref, not as it sits in the worktree.

        The form must describe the run that will actually execute; a local edit or
        a different checkout would otherwise build one shape and dispatch another.
        """
        import base64

        # The contents API returns base64 with embedded newlines, and --jq would
        # hand back that raw field; the whole document is read and decoded here.
        payload = self.json(["api", f"repos/{repo}/contents/{path}?ref={ref}"])
        encoded = str(_as_object(payload, path).get("content", ""))
        return base64.b64decode(encoded).decode("utf-8")

    def run_view(self, run_id: str, fields: str) -> dict[str, Any]:
        return dict(self.json(["run", "view", run_id, "--json", fields]))

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        payload = self.json(
            ["api", f"repos/{self.current_repo()}/actions/runs/{run_id}/artifacts?per_page=100"]
        )
        return [dict(a) for a in payload.get("artifacts", [])]

    def repo_artifacts(self, repo: str) -> list[dict[str, Any]]:
        payload = self.json(["api", f"repos/{repo}/actions/artifacts?per_page=100"])
        return [dict(a) for a in payload.get("artifacts", [])]
