from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from cangjie_build.errors import BuildError
from cangjie_build.logging_setup import get_logger
from cangjie_build.runner import run, run_capture

_log = get_logger("cangjie_build.git")


_FULL_SHA_RE = re.compile(r"\A[0-9a-fA-F]{40}\Z")
# The lengths git itself accepts for a shortened object name. Below seven
# characters `git rev-parse --short` never prints one, and a ref name is far
# more likely to look like four hex digits than seven.
_SHORT_SHA_RE = re.compile(r"\A[0-9a-fA-F]{7,39}\Z")

# A commit that only a pull/merge request reaches sits on no branch and no tag,
# which is exactly the revision a pre-merge build wants. GitHub files them under
# refs/pull, the GitLab-style forges (AtomGit, GitCode, Gitee) under
# refs/merge-requests. A pattern that matches nothing is not an error, so both
# are always requested and whichever exists is fetched.
_REQUEST_REFSPECS = (
    "+refs/pull/*/head:refs/remotes/origin/pull/*",
    "+refs/merge-requests/*/head:refs/remotes/origin/merge-requests/*",
)

# `git fetch` starts a background `maintenance --auto` (older gits: `gc --auto`)
# that keeps writing into .git after fetch exits. The resolution scratch
# repository is deleted as soon as it has answered, so that writer turns the
# cleanup into a race against itself and fails on a non-empty .git.
_NO_AUTO_MAINTENANCE = ("-c", "gc.auto=0", "-c", "maintenance.auto=false")


def check_ref(ref: str) -> str:
    """Refuse a ref that git would read as an option or that cannot name one."""
    if ref.startswith("-"):
        raise BuildError("git.ref", f"ref {ref!r} starts with '-', which git reads as an option")
    if any(ch.isspace() for ch in ref):
        raise BuildError("git.ref", f"ref {ref!r} contains whitespace and cannot name a revision")
    return ref


def resolve_commit(url: str, ref: str, *, scratch_root: Path) -> str:
    """Resolve ``ref`` to a full commit hash by fetching ``url``'s refs.

    A shortened hash cannot be asked of the server: fetch and upload-pack take
    full hashes and ref names only, so a prefix reaches them as a ref named after
    itself and comes back "not found". Resolving locally needs the commits that
    hash can abbreviate, which is why every branch, tag and request head is
    fetched. ``--filter=tree:0`` keeps that to a couple of megabytes: the commit
    graph is downloaded, the file contents stay on the server.

    Branches are fetched into ``refs/heads`` rather than ``refs/remotes``: a
    branch may legitimately be named like a hash (``deadbeef``), and only a ref
    of that name makes git try the ref before the abbreviated object name, which
    is the precedence a clone would use.
    """
    check_ref(ref)
    scratch_root.mkdir(parents=True, exist_ok=True)
    repo = Path(tempfile.mkdtemp(prefix="resolve-", dir=scratch_root))
    _log.info("Resolving %s in %s", ref, url)
    try:
        run(["git", "init", "--quiet", str(repo)], stage="git.init")
        run(["git", "-C", str(repo), "remote", "add", "origin", url], stage="git.remote")
        run(
            [
                "git",
                *_NO_AUTO_MAINTENANCE,
                "-C",
                str(repo),
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=tree:0",
                "origin",
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                *_REQUEST_REFSPECS,
            ],
            stage="git.refs",
        )
        rc, out = run_capture(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            stage="git.resolve",
            check=False,
        )
        sha = out.strip()
    finally:
        shutil.rmtree(repo, ignore_errors=True)
    if rc != 0 or not sha:
        raise BuildError(
            "git.resolve",
            f"{ref} does not name exactly one commit in {url}: it is on no branch, on no tag, "
            "and no commit hash there starts with it. Pass the full hash, or a branch or tag "
            "that exists in that repository",
        )
    _log.info("Resolved %s to %s", ref, sha)
    return sha


# Records which ref a checkout was made for, and the commit it resolved to. A
# commit-based clone is detached and keeps no ref, so neither a shortened hash
# nor a branch whose name looks like one can be resolved inside it later; the
# ref-to-commit answer is only available at clone time. Keeping both in
# .git/config makes a later reuse check possible without fetching again.
_REF_KEY = "cangjie-build.ref"
_COMMIT_KEY = "cangjie-build.commit"


def _write_source(dest: Path, ref: str, sha: str) -> None:
    for key, value in ((_REF_KEY, ref), (_COMMIT_KEY, sha)):
        run(["git", "-C", str(dest), "config", "--local", key, value], stage="git.config")


def _read_source(dest: Path) -> tuple[str, str] | None:
    out: list[str] = []
    for key in (_REF_KEY, _COMMIT_KEY):
        rc, value = run_capture(
            ["git", "-C", str(dest), "config", "--local", "--get", key],
            stage="git.config",
            check=False,
        )
        if rc != 0 or not value.strip():
            return None
        out.append(value.strip())
    return out[0], out[1]


def _clone_at_commit(url: str, dest: Path, ref: str, sha: str) -> None:
    """Create ``dest`` holding ``sha`` and nothing else.

    ``git clone --revision`` would do this in one step but only exists from git
    2.49, while the Ubuntu 22.04 build image ships 2.34.
    """
    _log.info("Cloning %s @ %s (%s) into %s", url, ref, sha, dest)
    run(["git", "init", "--quiet", str(dest)], stage="git.init")
    run(["git", "-C", str(dest), "remote", "add", "origin", url], stage="git.remote")
    run(
        ["git", *_NO_AUTO_MAINTENANCE, "-C", str(dest), "fetch", "--depth", "1", "origin", sha],
        stage="git.fetch",
    )
    run(
        ["git", "-C", str(dest), "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        stage="git.checkout",
    )
    _write_source(dest, ref, sha)


def shallow_clone(
    url: str, dest: Path, *, tag: str | None = None, scratch_root: Path | None = None
) -> None:
    """Shallow-clone ``url`` at ``tag`` into ``dest``. ``dest`` must not exist.

    ``tag`` may be a branch name, a tag, or a commit hash in any of the lengths
    git accepts. A hash needs its own route: ``git clone --branch`` resolves the
    name among the remote's refs, so a hash arrives at the server as a ref named
    after itself and the clone dies with "remote branch <hash> not found", which
    reads like a wrong hash rather than the wrong clone form. A full hash is
    fetched directly; a shortened one is resolved against the remote's refs
    first, then fetched in full.
    """
    if dest.exists():
        raise BuildError("git", f"destination already exists: {dest}")
    if tag:
        check_ref(tag)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if tag and _FULL_SHA_RE.match(tag):
        _clone_at_commit(url, dest, tag, tag.lower())
        return
    if tag and _SHORT_SHA_RE.match(tag):
        resolved = resolve_commit(url, tag, scratch_root=scratch_root or dest.parent)
        _clone_at_commit(url, dest, tag, resolved)
        return
    cmd: list[str] = ["git", "clone", "--depth", "1"]
    if tag:
        cmd += ["--branch", tag]
    cmd += [url, str(dest)]
    _log.info("Cloning %s%s into %s", url, f" @ {tag}" if tag else "", dest)
    run(cmd, stage="git.clone")


def head_commit(dest: Path, *, stage: str) -> str:
    """The commit ``dest`` currently has checked out."""
    rc, out = run_capture(
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
        stage=stage,
        check=False,
    )
    if rc != 0 or not out.strip():
        raise BuildError(
            stage,
            f"{dest} has no resolvable HEAD (interrupted clone?); remove it and re-run",
        )
    return out.strip()


def require_checkout(dest: Path, *, tag: str | None, stage: str) -> None:
    """Reject a directory that is not a usable checkout of ``tag``.

    A clone killed mid-fetch leaves the directory behind with a partial object
    store, and a directory cloned from a different revision carries the wrong
    sources. Either one makes downstream failures look like something else, so
    the directory is refused with the remedy in the message instead of being
    reused silently.

    The check is against the commit ``HEAD`` points at, not against whether the
    name exists locally: an existing checkout can hold a ref for some other
    revision, and it is ``HEAD`` that gets built.
    """
    if not (dest / ".git").exists():
        raise BuildError(stage, f"{dest} exists but is not a git checkout; remove it and re-run")
    if not (dest / ".git" / "HEAD").exists():
        raise BuildError(
            stage,
            f"{dest} looks like an interrupted clone (no .git/HEAD); remove it and re-run",
        )
    head = head_commit(dest, stage=stage)
    if not tag:
        return
    rc, out = run_capture(
        ["git", "-C", str(dest), "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
        stage=stage,
        check=False,
    )
    if rc == 0 and out.strip():
        if out.strip() != head:
            raise BuildError(
                stage,
                f"{dest} is checked out at {head[:12]}, not at {tag} ({out.strip()[:12]}). "
                f"Remove {dest} to re-clone, or drop the ref",
            )
        return
    # The name resolves to nothing inside the checkout. A detached clone has no
    # refs at all, and a caller may be naming a commit by a prefix of its hash.
    recorded = _read_source(dest)
    if recorded is not None:
        if recorded[0] != tag:
            raise BuildError(
                stage,
                f"{dest} was cloned at {recorded[0]} ({recorded[1][:12]}), not at {tag}. "
                f"Remove {dest} to re-clone, or drop the ref",
            )
        if recorded[1] == head:
            return
        raise BuildError(
            stage,
            f"{dest} was cloned at {tag} ({recorded[1][:12]}) but its HEAD is now "
            f"{head[:12]}. Remove {dest} to re-clone, or drop the ref",
        )
    if (_FULL_SHA_RE.match(tag) or _SHORT_SHA_RE.match(tag)) and head.lower().startswith(
        tag.lower()
    ):
        # Checkouts made before the record existed: a hash still names the commit
        # that is checked out when it abbreviates its hash.
        return
    raise BuildError(
        stage,
        f"{dest} is at {head[:12]} and cannot confirm it was cloned at {tag}; "
        f"remove it to re-clone at {tag}, or drop the ref",
    )
