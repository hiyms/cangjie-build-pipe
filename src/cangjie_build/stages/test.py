from __future__ import annotations

import json
import os
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Literal, cast

from cangjie_build.config import BuildConfig
from cangjie_build.errors import BuildError
from cangjie_build.git import shallow_clone
from cangjie_build.logging_setup import get_logger, stage
from cangjie_build.runner import run as run_cmd
from cangjie_build.runner import run_capture
from cangjie_build.stages._common import (
    ensure_dir,
    join_pathsep,
    python_exe,
    require_dir,
    require_file,
)

_log = get_logger("cangjie_build.stages.test")

# Upstream defaults. Both suites are separate repositories; the conformance
# suite lives inside cangjie_test alongside the HLT/LLT trees.
DEFAULT_TEST_REPO = "https://github.com/CangjieLanguage/cangjie_test.git"
DEFAULT_FRAMEWORK_REPO = "https://github.com/CangjieLanguage/cangjie_test_framework.git"

Suite = Literal["conformance", "hlt", "llt"]

# The two Maple suites do not share a config layout: HLT keeps a per-host/target
# matrix of configs under linux_x64-linux_x64/, while LLT has one flat
# cjnative_test.cfg that carries its own complete [internal-var] block. Each
# suite therefore names its own file and its own test list.
_SUITE_CONFIG: dict[str, tuple[str, str]] = {
    "hlt": (
        "configs/cjnative/linux_x64-linux_x64/basic.cfg",
        "testlist",
    ),
    "llt": (
        "configs/cjnative/cjnative_test.cfg",
        "cjnative_testlist",
    ),
}


def checkout_repo(url: str, dest: Path, *, tag: str | None, stage_name: str) -> Path:
    """Shallow-clone ``url`` into ``dest`` unless a real checkout is already there.

    The test repositories are only needed on the machine that runs the tests,
    which is a different machine from the one that compiled the SDK. They are
    therefore cloned here rather than in the fetch stage, and a pre-existing
    checkout is reused so a re-run does not re-download 1 GB of cases.

    A directory is only reused when it is actually a git checkout: an
    interrupted clone leaves the directory behind, and silently reusing it
    produces confusing downstream failures (``not a git repository``, empty
    discovery). A requested ``tag`` that the existing checkout does not have is
    reported rather than ignored, since testing the wrong revision silently is
    worse than stopping.
    """
    if dest.is_dir():
        # `.git` alone is not proof of a usable checkout: a clone killed mid-fetch
        # leaves one with a partial object store and no worktree, and reusing it
        # only moves the failure into `git diff-tree` or empty discovery.
        if not (dest / ".git").exists():
            raise BuildError(
                stage_name,
                f"{dest} exists but is not a git checkout; remove it and re-run",
            )
        if not (dest / ".git" / "HEAD").exists():
            raise BuildError(
                stage_name,
                f"{dest} looks like an interrupted clone (no .git/HEAD); remove it and re-run",
            )
        head_rc, _ = run_capture(
            ["git", "rev-parse", "--verify", "--quiet", "HEAD"],
            cwd=dest,
            stage=stage_name,
            check=False,
        )
        if head_rc != 0:
            raise BuildError(
                stage_name,
                f"{dest} has no resolvable HEAD (interrupted clone?); remove it and re-run",
            )
        if tag:
            rc, _ = run_capture(
                ["git", "rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}"],
                cwd=dest,
                stage=stage_name,
                check=False,
            )
            if rc != 0:
                # A warning would let the suites run a different revision than
                # the caller asked for while reporting success.
                raise BuildError(
                    stage_name,
                    f"{dest} does not contain {tag}; it was cloned from another "
                    f"revision. Remove {dest} to re-clone, or drop --test-tag",
                )
        _log.info("%s already at %s, reusing", stage_name, dest)
        return dest
    # shallow_clone understands a bare commit SHA as well as a branch, which
    # matters because a pull request's cases exist only as a commit.
    shallow_clone(url, dest, tag=tag)
    return require_dir(dest, stage=stage_name)


def sdk_env(cfg: BuildConfig) -> dict[str, str]:
    """Environment a test suite needs to find the toolchain under test.

    Reads the packaged SDK (``software/cangjie``), which is what the CI test job
    unpacks from the build job's artifact, not the compiler's build tree.
    """
    cangjie_home = require_dir(cfg.software_dir / "cangjie", stage="test.sdk")
    # Validated, not just computed: if the layout ever drifts, failing here beats
    # every case dying with a loader error.
    runtime_lib = require_dir(
        cangjie_home / "runtime" / "lib" / cfg.target.installed_runtime_lib_subdir(),
        stage="test.sdk",
    )
    # package._package_stdx stages the tree as software/<stdx_target_subdir>, so
    # the directory name already carries the platform; there is no extra
    # cangjie_stdx prefix to join onto.
    # Only exported when the archive was unpacked. STDX is a separate artifact,
    # so requiring it here would break runs that deliberately test the SDK alone.
    stdx_dir = cfg.software_dir / cfg.target.stdx_target_subdir()

    # join_pathsep drops empty parts. For LD_LIBRARY_PATH an empty component
    # means "the current directory", and the cases run with their work directory
    # as CWD, so a stray .so there would shadow a toolchain library. PATH treats
    # an empty component as EACCES rather than CWD, but a trailing separator is
    # still noise worth dropping.
    env = {
        "CANGJIE_HOME": str(cangjie_home),
        "PATH": join_pathsep(
            str(cangjie_home / "bin"),
            str(cangjie_home / "tools" / "bin"),
            os.environ.get("PATH", ""),
        ),
        "LD_LIBRARY_PATH": join_pathsep(
            str(runtime_lib),
            str(cangjie_home / "tools" / "lib"),
            os.environ.get("LD_LIBRARY_PATH", ""),
        ),
    }
    if stdx_dir.is_dir():
        env["CANGJIE_STDX_PATH"] = str(stdx_dir)
    return env


def _run_conformance(
    cfg: BuildConfig,
    repo: Path,
    framework: Path,
    work: Path,
    *,
    tests: list[str],
    base_timeout: int,
) -> Path:
    """Run the SPEC conformance suite through cangjie_test's own harness.

    ``harness.py`` always exits 0 — a failing case only shows up in
    ``results.log.json``. The stage therefore reads that JSON back and raises on
    any FAILED, ERRORED or INCOMPLETE entry so the caller gets a non-zero status.
    """
    harness_dir = require_dir(
        repo / "Conformance" / "Compiler" / "harness", stage="test.conformance.harness"
    )
    testsuite = require_dir(
        repo / "Conformance" / "Compiler" / "testsuite", stage="test.conformance.suite"
    )
    work = ensure_dir(work)
    # The harness appends to its metadata file rather than truncating it, so a
    # leftover from an earlier run would be merged into this run's tally.
    stale = work / "results.log.json"
    if stale.exists():
        stale.unlink()
    cmd: list[str] = [
        python_exe(),
        str(harness_dir / "harness.py"),
        "--work-dir",
        str(work),
        "--test-root",
        str(testsuite),
        "--log-mode",
        "progress",
        "--base-timeout",
        str(base_timeout),
        # Cases that expect clean compilation fail on unused-symbol warnings;
        # this is the flag upstream's README passes for the same reason.
        "--cjc-flags=-Woff unused",
        "--no-color",
    ]
    if tests:
        cmd += ["--tests", *tests]
    run_cmd(cmd, cwd=harness_dir, env_overlay=sdk_env(cfg), stage="test.conformance")

    results = work / "results.log.json"
    require_file(results, stage="test.conformance.results")
    return results


def read_conformance_totals(results: Path) -> dict[str, int]:
    """Tally ``results.log.json``.

    The file is a JSON array: element 0 is the config dict, element 1 is the
    driver registry, elements 2..n-2 are per-test dicts, and the last element
    is ``{"total_time": …}``. Only the per-test entries carry ``result``.
    """
    data = cast(object, json.loads(results.read_text(encoding="utf-8")))
    if not isinstance(data, list):
        raise BuildError("test.conformance.results", f"unexpected shape in {results}")
    entries = cast("list[object]", data)
    totals: dict[str, int] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        record = cast("dict[str, object]", entry)
        if not isinstance(record.get("test_path"), str):
            continue
        result = record.get("result")
        if isinstance(result, str):
            totals[result] = totals.get(result, 0) + 1
    return totals


def _resolve_under(root: Path, path: Path, *, stage: str) -> Path:
    """Resolve ``path`` to something the framework can open, then require it.

    Three spellings have to work: absolute, relative to the caller's working
    directory (``./scratch/case.cj``), and relative to the ``cangjie_test``
    checkout (``testsuites/LLT/...``, which is what the README shows). The
    framework runs with its own working directory, so none of these reach it
    unchanged.
    """
    if path.is_absolute():
        candidate = path
    elif path.exists():
        candidate = path.resolve()
    else:
        candidate = root / path
    if candidate.is_dir():
        return require_dir(candidate, stage=stage)
    return require_file(candidate, stage=stage)


def run_mapple(
    cfg: BuildConfig,
    repo: Path,
    framework: Path,
    work: Path,
    *,
    suite: Literal["hlt", "llt"],
    test_list: Path | None,
    cases: list[Path],
    jobs: int,
    base_timeout: int,
) -> Path:
    """Run an HLT/LLT suite through the Maple framework (``cangjie_test_framework``).

    Unlike the conformance harness this one honours ``--fail_exit``, so a
    failing case already yields a non-zero process status. ``--json_output``
    is written alongside for the same summary the conformance path produces.

    ``cases`` are positional test paths and are mutually exclusive with
    ``test_list``: the framework treats a positional path as a literal case and
    skips test-list expansion, while ``--test_list`` replaces the suite's own
    list. Passing both would make the list filter a single file that the
    positional argument already pins down.

    Every path handed to the framework is made absolute first. Maple resolves
    relative paths against the process working directory, which is ``framework``
    here, not the ``cangjie_test`` checkout a caller would naturally write paths
    against.
    """
    suite_dir = require_dir(repo / "testsuites" / suite.upper(), stage=f"test.{suite}.suite")
    cfg_rel, list_rel = _SUITE_CONFIG[suite]
    cfg_file = require_file(suite_dir / cfg_rel, stage=f"test.{suite}.cfg")
    main_py = require_file(framework / "main.py", stage=f"test.{suite}.framework")
    work = ensure_dir(work)
    # Remove the previous run's report first. If this invocation fails before
    # Maple writes one, reading the leftover would report the old verdict as
    # this run's result.
    results_file = work / "results.json"
    if results_file.exists():
        results_file.unlink()
    env = sdk_env(cfg)
    env["CANGJIE_TEST"] = str(repo)
    cmd: list[str] = [
        python_exe(),
        str(main_py),
        f"--test_cfg={cfg_file}",
        f"--temp_dir={work / 'run'}",
        f"--log_dir={work / 'log'}",
        f"--json_output={results_file}",
        f"-j{jobs}",
        f"--timeout={base_timeout}",
        "--fail_exit",
    ]
    if cases:
        resolved = [_resolve_under(repo, c, stage=f"test.{suite}.case") for c in cases]
        files = [c for c in resolved if c.is_file()]
        dirs = [c for c in resolved if c.is_dir()]
        # Maple discovers cases by walking a test list, so directories are fed
        # through a generated list of their suite-relative paths. Which file
        # inside a directory is the runnable case stays the framework's decision.
        # Plain files are passed positionally, which pins exactly that case.
        if dirs:
            # Everything goes through the generated list once a directory is
            # involved: a --test_list filters what the framework will consider,
            # so positional paths added alongside it would be dropped and the
            # run would silently cover less than asked. Listing each file's own
            # directory is exact for a file and covers the directory case too.
            entries: list[str] = [d.relative_to(suite_dir).as_posix() + "/" for d in dirs]
            entries += [
                f.parent.relative_to(suite_dir).as_posix() + "/"
                for f in files
                if f.is_relative_to(suite_dir)
            ]
            list_file = work / "selection.list"
            list_file.write_text(
                "[ALL-TEST-CASE]\n" + "\n".join(sorted(set(entries))) + "\n[EXCLUDE-TEST-CASE]\n",
                encoding="utf-8",
            )
            cmd.append(f"--test_list={list_file}")
        elif files:
            # Files only: positional paths pin exactly those cases.
            cmd += [str(f) for f in files]
        if dirs or not files:
            cmd.append(str(suite_dir))
    else:
        list_path = (
            _resolve_under(repo, test_list, stage=f"test.{suite}.list")
            if test_list is not None
            else require_file(suite_dir / list_rel, stage=f"test.{suite}.list")
        )
        cmd += [
            f"--test_list={require_file(list_path, stage=f'test.{suite}.list')}",
            str(suite_dir),
        ]
    run_cmd(cmd, cwd=framework, env_overlay=env, stage=f"test.{suite}")
    return require_file(results_file, stage=f"test.{suite}.results")


def read_mapple_totals(results: Path) -> dict[str, int]:
    """Tally the Maple ``--json_output`` file (one entry per test-list section)."""
    data = cast(object, json.loads(results.read_text(encoding="utf-8")))
    if not isinstance(data, list):
        raise BuildError("test.results", f"unexpected shape in {results}")
    sections = cast("list[object]", data)
    totals: dict[str, int] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        record = cast("dict[str, object]", section)
        count = record.get("total")
        if not isinstance(count, int):
            continue
        totals["total"] = totals.get("total", 0) + count
        for status in ("PASS", "FAIL", "UNRESOLVED", "NOT_RUN"):
            value = record.get(status)
            if isinstance(value, int) and value:
                totals[status] = totals.get(status, 0) + value
    return totals


def _git(*args: str, cwd: Path, stage: str, check: bool = True) -> tuple[int, str]:
    return run_capture(["git", *args], cwd=cwd, stage=stage, check=check)


def _ensure_rev_present(repo: Path, rev: str) -> None:
    """Make ``rev`` diffable in a possibly shallow checkout.

    The test repository is cloned with ``--depth 1``. In a shallow clone the
    tip commit is a grafted boundary, so ``diff-tree`` reports no paths for it,
    and any older revision is simply absent. Deepening until the revision has a
    parent makes both the tip and recent history diffable while keeping the
    clone small.
    """
    rc, _ = _git("cat-file", "-e", f"{rev}^{{commit}}", cwd=repo, stage="test.changed", check=False)
    if rc != 0:
        # Not in the local history; ask the remote for more of it.
        _log.info("Rev %s is not in the shallow checkout; deepening", rev)
        fetch_rc, fetch_out = _git(
            "fetch", "--deepen=50", "origin", cwd=repo, stage="test.changed", check=False
        )
        if fetch_rc != 0:
            raise BuildError(
                "test.changed",
                f"rev {rev} is unknown locally and deepening from origin failed: {fetch_out.strip()}",
            )
        rc, _ = _git(
            "cat-file", "-e", f"{rev}^{{commit}}", cwd=repo, stage="test.changed", check=False
        )
        if rc != 0:
            raise BuildError(
                "test.changed",
                f"rev {rev} does not resolve in {repo} even after deepening; "
                "pass a shallow-clone-visible rev such as HEAD or a newer tag",
            )
    depth_rc, _ = _git(
        "rev-parse", "--verify", "--quiet", f"{rev}^", cwd=repo, stage="test.changed", check=False
    )
    if depth_rc != 0:
        # A boundary commit has no parent, so diff-tree prints nothing. Deepen
        # inside a conditional so a missing parent does not abort the script.
        _git("fetch", "--deepen=2", "origin", cwd=repo, stage="test.changed")


def cases_touched_by(cfg: BuildConfig, rev: str, suites: list[Suite]) -> list[Path]:
    """Resolve the locations a cangjie_test commit touched.

    Runs ``git diff-tree`` against the checkout the suites will use and keeps the
    ``.cj`` and ``.info`` files under ``testsuites/`` for the requested suites.
    Returns their containing directories: which file inside a directory is the
    runnable case is the framework's decision, not this stage's. A commit that
    touched nothing runnable yields an error rather than an empty run, because an
    empty selection would otherwise pass silently.

    Only modified files count, not deleted ones: a deleted case cannot be run.
    """
    repo = cfg.workspace / "cangjie_test_checkouts" / "cangjie_test"
    require_dir(repo, stage="test.changed")
    wanted = {s.upper() for s in suites if s in ("hlt", "llt")}
    if not wanted:
        raise BuildError(
            "test.changed",
            f"--changed-in needs an HLT or LLT suite; got {suites}",
        )
    _ensure_rev_present(repo, rev)
    # Merge commits need -m. Without it `diff-tree -r` prints nothing at all for a
    # merge; with it, the diff against each parent is emitted, so a merge's own
    # added and modified cases show up. Releases on the upstream dev branch are
    # almost always merges, so refusing them would make --changed-in useless
    # against the branch the workflow actually clones.
    _, stdout = _git(
        "diff-tree",
        "--no-commit-id",
        "--name-only",
        "--diff-filter=d",
        "-r",
        "-m",
        rev,
        cwd=repo,
        stage="test.changed",
    )
    # Selection is by directory, not by inspecting case files. Deciding whether
    # a file is a runnable case is the framework's job: tool suites put the case
    # in a sibling .info and use the .cj as input data, and only Maple's own
    # directive parser knows that. Handing it the containing directory lets it
    # discover the right file, and a file-carrying EXEC is still picked up
    # directly by passing the file itself.
    selected: set[Path] = set()
    for line in stdout.splitlines():
        parts = Path(line).parts
        if len(parts) < 3 or parts[0] != "testsuites" or parts[1] not in wanted:
            continue
        candidate = repo / line
        if not candidate.exists():
            continue
        if candidate.suffix in {".cj", ".info"}:
            # The case file itself, or the input data of an .info-driven case:
            # in both cases the enclosing directory is what selects the case.
            selected.add(candidate.parent)
        else:
            # Configs and docs are not cases; ignore them.
            continue
    if not selected:
        raise BuildError(
            "test.changed",
            f"commit {rev} changed nothing under testsuites/{'/'.join(sorted(wanted))}; "
            "only modified files count, not deleted or renamed-away ones",
        )
    paths = sorted(selected)
    _log.info("Commit %s touched %d location(s):", rev, len(paths))
    for path in paths:
        _log.info("  %s", path.relative_to(repo))
    return paths


def stage_sdk_from_archives(cfg: BuildConfig, archives: list[Path]) -> None:
    """Unpack SDK/STDX archives into ``software/`` so the suites can use them.

    The test job starts from the build job's artifact rather than a build tree,
    so this restores the same layout ``package`` produced: archive members are
    ``cangjie/`` and ``<stdx_target_subdir>/``, both rooted at ``software/``.
    """
    if not archives:
        raise BuildError("test.unpack", "no SDK/STDX archives given")
    ensure_dir(cfg.software_dir)
    # Start from a clean tree: extracting a second archive over the first would
    # silently mix two SDK versions.
    existing = cfg.software_dir / "cangjie"
    if existing.exists():
        shutil.rmtree(existing)
    for archive in archives:
        require_file(archive, stage="test.unpack")
        _log.info("Unpacking %s into %s", archive, cfg.software_dir)
        # Archives can come from a user-supplied URL, so refuse members that
        # would escape the target directory.
        _safe_extract(archive, cfg.software_dir)
    cangjie_home = require_dir(cfg.software_dir / "cangjie", stage="test.unpack")
    # A Windows SDK satisfies the directory check above, and the mismatch would
    # then only show up as loader failures in every case. Require the
    # target-specific pieces up front.
    require_file(cangjie_home / "bin" / f"cjc{cfg.target.spec.exe_suffix}", stage="test.unpack")
    require_dir(
        cangjie_home / "runtime" / "lib" / cfg.target.installed_runtime_lib_subdir(),
        stage="test.unpack",
    )


def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract ``archive`` into ``dest``, rejecting members that would escape it.

    ``filter="data"`` is the stdlib's own member validation (absolute paths,
    ``..`` components, links pointing outside). It runs inside the same open as
    the extraction, so there is no window in which the archive could be swapped
    for an unvalidated one. Available since 3.11.4, and this project pins
    ``>=3.11,<3.13``.
    """
    try:
        shutil.unpack_archive(str(archive), str(dest), filter="data")
    except (tarfile.TarError, zipfile.BadZipFile) as exc:
        raise BuildError("test.unpack", f"{archive} is not a readable archive: {exc}") from exc


_SUITE_PREFIX: dict[str, Suite] = {"HLT": "hlt", "LLT": "llt"}


def suite_of(path: Path, repo: Path) -> Suite | None:
    """Which suite a case path belongs to, from its position in the checkout.

    Callers should not have to state the suite separately: a path that already
    says ``testsuites/LLT/...`` carries that information, and requiring both is
    how a mismatched pair (suite=hlt with an LLT path) becomes possible.

    Relative paths are anchored on the checkout, matching how they are later
    resolved for the framework.
    """
    candidate = path if path.is_absolute() else repo / path
    options = [candidate.resolve(), path.resolve()]
    for option in options:
        try:
            rel = option.relative_to(repo.resolve())
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) >= 2 and parts[0] == "testsuites":
            return _SUITE_PREFIX.get(parts[1])
        if len(parts) >= 2 and parts[0] == "Conformance":
            return "conformance"
    return None


def group_cases(cases: list[Path], repo: Path) -> dict[Suite, list[Path]]:
    """Group case paths by suite, rejecting anything that cannot be attributed."""
    grouped: dict[Suite, list[Path]] = {}
    unknown: list[Path] = []
    for case in cases:
        suite = suite_of(case, repo)
        if suite is None:
            unknown.append(case)
            continue
        grouped.setdefault(suite, []).append(case)
    if unknown:
        raise BuildError(
            "test",
            "cannot tell which suite these belong to (expected a path under "
            "testsuites/HLT, testsuites/LLT or Conformance): " + ", ".join(str(p) for p in unknown),
        )
    return grouped


def run(
    cfg: BuildConfig,
    *,
    suites: list[Suite],
    test_repo: str = DEFAULT_TEST_REPO,
    framework_repo: str = DEFAULT_FRAMEWORK_REPO,
    test_tag: str | None = None,
    framework_tag: str | None = None,
    tests: list[str] | None = None,
    test_list: Path | None = None,
    cases: list[Path] | None = None,
    changed_in: str | None = None,
    jobs: int = 8,
    base_timeout: int = 120,
) -> None:
    """Run cangjie_test suites against the SDK already staged in ``software/``.

    ``suites`` is the default set. Explicit ``cases`` override it: each case
    path already names its suite, so one invocation can mix HLT and LLT cases.
    Each suite still runs as its own framework invocation, because they use
    different config files, work directories and result files.

    Only meaningful for targets that can execute on this host; a cross-compiled
    Windows SDK cannot be exercised from the Linux builder.
    """
    if cfg.target.spec.cross_compile:
        raise BuildError(
            "test",
            f"target {cfg.target.spec.key} cannot run its own binaries on this host",
        )
    tests = tests or []
    cases = list(cases or [])
    with stage("test"):
        checkout_root = cfg.workspace / "cangjie_test_checkouts"
        repo = checkout_repo(
            test_repo, checkout_root / "cangjie_test", tag=test_tag, stage_name="test.repo"
        )
        # The two repositories have unrelated histories, so a revision named for
        # one does not exist in the other. Reusing test_tag here is what made a
        # commit from cangjie_test fail the framework fetch; without an explicit
        # framework_tag the framework simply follows its own default branch.
        framework = checkout_repo(
            framework_repo,
            checkout_root / "cangjie_test_framework",
            tag=framework_tag,
            stage_name="test.framework",
        )
        work_root = ensure_dir(cfg.workspace / "test-results")

        # Resolved after the checkout exists, so --changed-in always diffs the
        # same revision the suites will run.
        if changed_in:
            if cases:
                raise BuildError("test", "--changed-in picks the cases itself; drop --case")
            cases = cases_touched_by(cfg, changed_in, suites)

        # Explicit paths decide which suites run. With none, the caller's list is
        # the plan: that is the whole-suite mode.
        plan: dict[Suite, list[Path]] = {}
        if cases:
            if tests:
                raise BuildError("test", "--tests and --case cannot be combined")
            plan = group_cases(cases, repo)
        else:
            for suite in suites:
                plan[suite] = []

        for suite in plan:
            if suite == "conformance":
                results = _run_conformance(
                    cfg,
                    repo,
                    framework,
                    work_root / "conformance",
                    tests=tests,
                    base_timeout=base_timeout,
                )
                totals = read_conformance_totals(results)
                _log.info("conformance results: %s", totals)
                if not totals:
                    # A --tests mask that matches nothing would otherwise read
                    # as a clean run; no case executed means no evidence.
                    raise BuildError(
                        "test.conformance",
                        f"no cases ran (tests={tests or 'suite default'}; see {results})",
                    )
                bad = {
                    k: v
                    for k, v in totals.items()
                    if k in {"FAILED", "ERRORED", "INCOMPLETE"} and v
                }
                if bad:
                    raise BuildError(
                        "test.conformance",
                        f"failing cases: {bad} (see {results})",
                    )
                continue

            suite_cases = plan.get(suite, [])
            if tests:
                raise BuildError(
                    "test",
                    "--tests selects paths inside the conformance suite; "
                    f"use --test-list or --case for {suite.upper()}",
                )
            if suite_cases and test_list:
                raise BuildError(
                    "test",
                    "--case pins individual cases and --test-list replaces the "
                    "suite's list; pass one of them, not both",
                )
            if test_list is not None and len(plan) > 1:
                # Silently dropping it here would run the whole suite instead of
                # the filtered selection, which is a different and far more
                # expensive run than the caller asked for.
                raise BuildError(
                    "test",
                    "--test-list names cases in one suite; it cannot be combined "
                    f"with a selection spanning {sorted(plan)}. Use --case, or run "
                    "one suite at a time",
                )
            results = run_mapple(
                cfg,
                repo,
                framework,
                work_root / suite,
                suite=suite,
                test_list=test_list,
                cases=suite_cases,
                jobs=jobs,
                base_timeout=base_timeout,
            )
            totals = read_mapple_totals(results)
            _log.info("%s results: %s (details in %s)", suite.upper(), totals, results)
            # Maple's `total` counts UNRESOLVED and NOT_RUN too, and --fail_exit
            # ignores both, so a selection made only of helper files (no EXEC
            # header) exits 0 having executed nothing. Require at least one case
            # that actually ran.
            executed = totals.get("PASS", 0) + totals.get("FAIL", 0)
            if not executed:
                selected = (
                    ", ".join(str(c) for c in suite_cases)
                    if suite_cases
                    else (test_list or "suite default")
                )
                raise BuildError(
                    f"test.{suite}",
                    f"no case actually executed (selection: {selected}; "
                    f"tallies: {totals}; see {results})",
                )


def default_test_list(repo: Path, suite: Literal["hlt", "llt"]) -> Path:
    """Path of the suite's own test list (the cases CI is expected to run)."""
    name = "testlist" if suite == "hlt" else "cjnative_testlist"
    return require_file(repo / "testsuites" / suite.upper() / name, stage=f"test.{suite}.list")


def clear_work(work: Path) -> None:
    """Drop a previous run's work directory (harness needs an empty bin/)."""
    if work.exists():
        shutil.rmtree(work)


# VM sizing ladder for the test machine. Cases are independent processes, so
# wall time scales with cores until the framework's own per-case compilation
# stops benefiting; picking the smallest tier that fits keeps Spot spend down.
SIZE_TIERS: tuple[tuple[int, str], ...] = (
    (20, "Standard_F2als_v7"),
    (1000, "Standard_F4als_v7"),
    (10**9, "Standard_F8als_v7"),
)


def checkout_test_repo(
    cfg: BuildConfig, *, test_repo: str, test_tag: str | None, stage_name: str
) -> Path:
    """Clone (or reuse) just the cangjie_test checkout, without an SDK or suite run."""
    return checkout_repo(
        test_repo,
        cfg.workspace / "cangjie_test_checkouts" / "cangjie_test",
        tag=test_tag,
        stage_name=stage_name,
    )


def size_for_case_count(count: int) -> str:
    """Smallest VM in :data:`SIZE_TIERS` that covers ``count`` cases."""
    if count < 1:
        raise BuildError("test.plan", f"case count must be positive, got {count}")
    for limit, size in SIZE_TIERS:
        if count <= limit:
            return size
    raise BuildError("test.plan", f"no size tier covers {count} cases")


def count_cases(paths: list[Path], repo: Path) -> int:
    """Upper bound on the number of cases reachable from the selected paths.

    Only used to choose a VM size, so it deliberately over-counts: guessing which
    file inside a directory is the runnable case means re-implementing the
    framework's directive parser, and guessing wrong is asymmetric. An undercount
    puts hundreds of cases on a 2 vCPU box (slow, and it can trip
    ``--base-timeout``); an overcount costs one tier of Spot price.

    Tool suites keep an ``.info`` case beside the ``.cj`` files it drives, so the
    two counts overlap rather than add. Taking the larger of the two is the
    cheapest rule that cannot undercount.
    """
    total = 0
    for path in paths:
        if path.is_file():
            total += 1
            continue
        infos = sum(1 for p in path.rglob("*.info") if p.is_file())
        cjs = sum(1 for p in path.rglob("*.cj") if p.is_file())
        total += max(infos, cjs)
    return total
