from __future__ import annotations

import re
from pathlib import Path

from cangjie_build.errors import BuildError
from cangjie_build.logging_setup import get_logger
from cangjie_build.runner import run

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
