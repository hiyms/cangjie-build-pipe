from __future__ import annotations

import re
from pathlib import Path

from cangjie_build.errors import BuildError
from cangjie_build.logging_setup import get_logger
from cangjie_build.runner import run, run_capture

_log = get_logger("cangjie_build.git")


_COMMIT_RE = re.compile(r"\A[0-9a-f]{40}\Z")


def shallow_clone(url: str, dest: Path, *, tag: str | None = None) -> None:
    """Shallow-clone ``url`` into ``dest``. ``dest`` must not already exist.

    ``tag`` may be a branch, a tag, or a full 40-character commit SHA. Fetching a
    bare SHA is not what ``git clone --branch`` accepts, and it needs the
    init/fetch/checkout dance; that path exists because a pull request head is
    only reachable by SHA. ``git clone --revision`` would do it in one step but
    only exists from git 2.49, while the Ubuntu 22.04 build image ships 2.34.
    """
    if dest.exists():
        raise BuildError("git", f"destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if tag and _COMMIT_RE.match(tag):
        _log.info("Cloning %s @ commit %s into %s", url, tag, dest)
        run(["git", "init", "--quiet", str(dest)], stage="git.init")
        run(["git", "-C", str(dest), "remote", "add", "origin", url], stage="git.remote")
        run(
            ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", tag],
            stage="git.fetch",
        )
        run(["git", "-C", str(dest), "checkout", "--quiet", "FETCH_HEAD"], stage="git.checkout")
        return
    cmd: list[str] = ["git", "clone", "--depth", "1"]
    if tag:
        cmd += ["--branch", tag]
    cmd += [url, str(dest)]
    _log.info("Cloning %s%s into %s", url, f" @ {tag}" if tag else "", dest)
    run(cmd, stage="git.clone")


def require_checkout(dest: Path, *, tag: str | None, stage: str) -> None:
    """Reject a directory that is not a usable checkout of ``tag``.

    A clone killed mid-fetch leaves the directory behind with a partial object
    store, and a directory cloned from a different revision carries the wrong
    sources. Either one makes downstream failures look like something else, so
    the directory is refused with the remedy in the message instead of being
    reused silently.
    """
    if not (dest / ".git").exists():
        raise BuildError(stage, f"{dest} exists but is not a git checkout; remove it and re-run")
    if not (dest / ".git" / "HEAD").exists():
        raise BuildError(
            stage,
            f"{dest} looks like an interrupted clone (no .git/HEAD); remove it and re-run",
        )
    head_rc, _ = run_capture(
        ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
        cwd=dest,
        stage=stage,
        check=False,
    )
    if head_rc != 0:
        raise BuildError(
            stage,
            f"{dest} has no resolvable HEAD (interrupted clone?); remove it and re-run",
        )
    if not tag:
        return
    rc, _ = run_capture(
        ["git", "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
        cwd=dest,
        stage=stage,
        check=False,
    )
    if rc != 0:
        raise BuildError(
            stage,
            f"{dest} does not contain {tag}; it was cloned from another revision. "
            f"Remove {dest} to re-clone, or drop the ref",
        )
