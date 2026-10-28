from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from cangjie_build.config import build_config
from cangjie_build.errors import BuildError
from cangjie_build.stages.test import (
    cases_touched_by,
    count_cases,
    default_test_list,
    group_cases,
    read_conformance_totals,
    read_mapple_totals,
    run_mapple,
    sdk_env,
    size_for_case_count,
    suite_of,
)


def _write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_conformance_totals_ignores_config_and_summary_entries(tmp_path: Path) -> None:
    """results.log.json is config, drivers, per-test dicts, then a summary."""
    results = _write(
        tmp_path / "results.log.json",
        [
            {"work_directory": "/w", "cjc": "/sdk/bin/cjc"},
            {"customs": {}, "groups": {}},
            {"test_path": "/t/a.cj", "result": "PASSED"},
            {"test_path": "/t/b.cj", "result": "PASSED"},
            {"test_path": "/t/c.cj", "result": "FAILED"},
            {"test_path": "/t/d.cj", "result": "ERRORED"},
            {"total_time": 1.5},
        ],
    )
    assert read_conformance_totals(results) == {"PASSED": 2, "FAILED": 1, "ERRORED": 1}


def test_conformance_totals_empty_run_yields_no_counts(tmp_path: Path) -> None:
    results = _write(tmp_path / "results.log.json", [{"cjc": "x"}, {}, {"total_time": 0.1}])
    assert read_conformance_totals(results) == {}


def test_conformance_totals_rejects_non_list(tmp_path: Path) -> None:
    results = _write(tmp_path / "results.log.json", {"cjc": "x"})
    with pytest.raises(BuildError, match="unexpected shape"):
        read_conformance_totals(results)


def test_mapple_totals_sums_sections(tmp_path: Path) -> None:
    results = _write(
        tmp_path / "results.json",
        [
            {"name": "HLT /a", "total": 3, "PASS": 2, "FAIL": 1, "tests": []},
            {"name": "HLT /b", "total": 2, "PASS": 2, "tests": []},
        ],
    )
    assert read_mapple_totals(results) == {"total": 5, "PASS": 4, "FAIL": 1}


def test_mapple_totals_empty_list_means_zero_cases(tmp_path: Path) -> None:
    results = _write(tmp_path / "results.json", [])
    assert read_mapple_totals(results) == {}


def test_default_test_list_reports_the_suite_file(tmp_path: Path) -> None:
    suite = tmp_path / "cangjie_test" / "testsuites"
    (suite / "HLT").mkdir(parents=True)
    (suite / "LLT").mkdir(parents=True)
    (suite / "HLT" / "testlist").write_text("[ALL-TEST-CASE]\n", encoding="utf-8")
    (suite / "LLT" / "cjnative_testlist").write_text("[ALL-TEST-CASE]\n", encoding="utf-8")

    assert default_test_list(tmp_path / "cangjie_test", "hlt").name == "testlist"
    assert default_test_list(tmp_path / "cangjie_test", "llt").name == "cjnative_testlist"

    (suite / "LLT" / "cjnative_testlist").unlink()
    with pytest.raises(BuildError, match="required file missing"):
        default_test_list(tmp_path / "cangjie_test", "llt")


def _git_repo(root: Path) -> Path:
    """Create a real git repo so the tests exercise the actual diff-tree call."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(root), "config", key, value], check=True)
    return root


def _commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)


def _cfg_for(tmp_path: Path):
    return build_config(workspace=tmp_path / "workspace", build_root=tmp_path / "bt")


def test_cases_touched_by_selects_suite_and_skips_deleted(tmp_path: Path) -> None:
    """Only modified .cj under the requested suite, and only files still present."""
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    repo.mkdir(parents=True)
    _git_repo(repo)

    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")

    hlt_case = repo / "testsuites" / "HLT" / "compiler" / "a.cj"
    llt_case = repo / "testsuites" / "LLT" / "compiler" / "b.cj"
    doomed = repo / "testsuites" / "LLT" / "compiler" / "gone.cj"
    cfg_file = repo / "testsuites" / "LLT" / "configs" / "cjnative" / "cjnative_test.cfg"
    for path in (hlt_case, llt_case, doomed):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// EXEC: %compiler %f -o %output\n", encoding="utf-8")
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text("// cfg\n", encoding="utf-8")
    _commit_all(repo, "add cases")

    # The target commit: modifies both suites, deletes one case, touches config.
    hlt_case.write_text("// EXEC: %compiler %f -o %output\n// edited\n", encoding="utf-8")
    llt_case.write_text("// EXEC: %compiler %f -o %output\n// edited\n", encoding="utf-8")
    doomed.unlink()
    cfg_file.write_text("// cfg\n", encoding="utf-8")
    _commit_all(repo, "edit cases")

    cfg = _cfg_for(tmp_path)
    assert cases_touched_by(cfg, "HEAD", ["llt"]) == [llt_case.parent]
    assert cases_touched_by(cfg, "HEAD", ["hlt", "llt"]) == [
        hlt_case.parent,
        llt_case.parent,
    ]


def test_cases_touched_by_rejects_commit_without_cases(tmp_path: Path) -> None:
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    repo.mkdir(parents=True)
    _git_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    (repo / "docs.md").write_text("notes\n", encoding="utf-8")
    _commit_all(repo, "docs only")

    with pytest.raises(BuildError, match=r"changed nothing under testsuites"):
        cases_touched_by(_cfg_for(tmp_path), "HEAD", ["llt"])


def test_cases_touched_by_resolves_merge_commit(tmp_path: Path) -> None:
    """A merge must list its own added cases, not report nothing."""
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    repo.mkdir(parents=True)
    _git_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")

    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "side"], check=True)
    side_case = repo / "testsuites" / "LLT" / "side.cj"
    side_case.parent.mkdir(parents=True, exist_ok=True)
    side_case.write_text("// EXEC: %compiler %f -o %output\n", encoding="utf-8")
    _commit_all(repo, "side")

    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-ff", "-m", "merge", "side"], check=True
    )

    # The merge itself introduces side.cj relative to its first parent.
    assert cases_touched_by(_cfg_for(tmp_path), "HEAD", ["llt"]) == [side_case.parent]


def test_cases_touched_by_reports_unknown_rev(tmp_path: Path) -> None:
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    repo.mkdir(parents=True)
    _git_repo(repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _commit_all(repo, "base")
    bogus = "0" * 40

    with pytest.raises(BuildError, match=r"unknown locally and deepening from origin failed"):
        cases_touched_by(_cfg_for(tmp_path), bogus, ["llt"])


def test_cases_touched_by_requires_a_mapple_suite(tmp_path: Path) -> None:
    cfg = build_config(workspace=tmp_path / "workspace", build_root=tmp_path / "bt")
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    repo.mkdir(parents=True)

    with pytest.raises(BuildError, match="needs an HLT or LLT suite"):
        cases_touched_by(cfg, "HEAD", ["conformance"])


def _fake_sdk(tmp_path: Path):
    """A minimal packaged-SDK layout, matching what `package` produces."""
    cfg = build_config(workspace=tmp_path / "workspace", build_root=tmp_path / "bt")
    home = cfg.software_dir / "cangjie"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "cjc").write_text("#!/bin/sh\n", encoding="utf-8")
    (home / "tools" / "bin").mkdir(parents=True)
    (home / "tools" / "lib").mkdir(parents=True)
    (home / "runtime" / "lib" / "linux_x86_64_cjnative").mkdir(parents=True)
    return cfg


def test_sdk_env_never_emits_empty_path_component(tmp_path: Path) -> None:
    """An empty PATH/LD_LIBRARY_PATH component means "current directory"."""
    cfg = _fake_sdk(tmp_path)
    for var in ("PATH", "LD_LIBRARY_PATH"):
        os.environ.pop(var, None)
    try:
        env = sdk_env(cfg)
    finally:
        pass
    for var in ("PATH", "LD_LIBRARY_PATH"):
        assert "" not in env[var].split(os.pathsep), f"{var}={env[var]!r}"
        assert not env[var].endswith(os.pathsep), f"{var}={env[var]!r}"


def test_sdk_env_requires_the_runtime_lib_dir(tmp_path: Path) -> None:
    cfg = _fake_sdk(tmp_path)
    shutil.rmtree(cfg.software_dir / "cangjie" / "runtime" / "lib" / "linux_x86_64_cjnative")
    with pytest.raises(BuildError, match="required directory missing"):
        sdk_env(cfg)


def test_run_mapple_anchors_relative_case_paths_on_the_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Maple runs with cwd=framework, so relative paths must be made absolute."""
    cfg = _fake_sdk(tmp_path)
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    framework = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test_framework"
    (repo / "testsuites" / "LLT" / "configs" / "cjnative").mkdir(parents=True)
    (repo / "testsuites" / "LLT" / "configs" / "cjnative" / "cjnative_test.cfg").write_text(
        "", encoding="utf-8"
    )
    case = repo / "testsuites" / "LLT" / "compiler" / "b.cj"
    case.parent.mkdir(parents=True)
    case.write_text("// case\n", encoding="utf-8")
    (framework).mkdir(parents=True)
    (framework / "main.py").write_text("", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run(cmd: list[object], **kwargs: object) -> int:
        seen.append([str(p) for p in cmd])
        # Maple writes its report itself; the stage requires it to exist.
        for arg in cmd:
            text = str(arg)
            if text.startswith("--json_output="):
                Path(text.split("=", 1)[1]).write_text("[]", encoding="utf-8")
        return 0

    monkeypatch.setattr("cangjie_build.stages.test.run_cmd", fake_run)
    run_mapple(
        cfg,
        repo,
        framework,
        tmp_path / "work",
        suite="llt",
        test_list=None,
        cases=[Path("testsuites/LLT/compiler/b.cj")],
        jobs=2,
        base_timeout=30,
    )
    argv = seen[0]
    assert str(case) in argv, argv
    assert all("cangjie_test_framework/testsuites" not in a for a in argv), argv


def test_run_mapple_passes_a_list_for_directory_cases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLT has no default `testlist`, so a directory needs one passed explicitly."""
    cfg = _fake_sdk(tmp_path)
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    framework = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test_framework"
    (repo / "testsuites" / "LLT" / "configs" / "cjnative").mkdir(parents=True)
    (repo / "testsuites" / "LLT" / "configs" / "cjnative" / "cjnative_test.cfg").write_text(
        "", encoding="utf-8"
    )
    (repo / "testsuites" / "LLT" / "cjnative_testlist").write_text(
        "[ALL-TEST-CASE]\n", encoding="utf-8"
    )
    case_dir = repo / "testsuites" / "LLT" / "compiler"
    case_dir.mkdir(parents=True)
    framework.mkdir(parents=True)
    (framework / "main.py").write_text("", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run(cmd: list[object], **kwargs: object) -> int:
        seen.append([str(p) for p in cmd])
        # Maple writes its report itself; the stage requires it to exist.
        for arg in cmd:
            text = str(arg)
            if text.startswith("--json_output="):
                Path(text.split("=", 1)[1]).write_text("[]", encoding="utf-8")
        return 0

    monkeypatch.setattr("cangjie_build.stages.test.run_cmd", fake_run)
    run_mapple(
        cfg,
        repo,
        framework,
        tmp_path / "work",
        suite="llt",
        test_list=None,
        cases=[case_dir],
        jobs=2,
        base_timeout=30,
    )
    argv = seen[0]
    # A generated selection list, not the suite's whole list.
    list_args = [a for a in argv if a.startswith("--test_list=")]
    assert len(list_args) == 1, argv
    generated = Path(list_args[0].split("=", 1)[1])
    assert generated.name == "selection.list"
    body = generated.read_text(encoding="utf-8")
    assert "Tools/cjfmt" not in body  # sanity: only the selected dir
    assert "compiler/" in body, body


def test_read_mapple_totals_counts_unresolved(tmp_path: Path) -> None:
    results = _write(
        tmp_path / "results.json",
        [{"name": "x", "total": 1, "PASS": 0, "FAIL": 0, "UNRESOLVED": 1, "tests": []}],
    )
    assert read_mapple_totals(results) == {"total": 1, "UNRESOLVED": 1}


def test_size_for_case_count_follows_the_ladder() -> None:
    assert size_for_case_count(1) == "Standard_F2als_v7"
    assert size_for_case_count(20) == "Standard_F2als_v7"
    assert size_for_case_count(21) == "Standard_F4als_v7"
    assert size_for_case_count(1000) == "Standard_F4als_v7"
    assert size_for_case_count(1001) == "Standard_F8als_v7"


def test_size_for_case_count_rejects_non_positive() -> None:
    with pytest.raises(BuildError, match="must be positive"):
        size_for_case_count(0)


def test_count_cases_never_undercounts_a_mixed_tree(tmp_path: Path) -> None:
    """One .info must not hide the plain .cj cases around it.

    Counting is only used to pick a VM size, so the rule takes the larger of the
    two counts. An undercount would put hundreds of cases on the smallest box.
    """
    repo = tmp_path / "repo"
    suite = repo / "testsuites" / "LLT" / "compiler" / "Sema"
    toolish = suite / "redefition" / "private_func"
    toolish.mkdir(parents=True)
    (toolish / "case.info").write_text("// EXEC: x\n", encoding="utf-8")
    (toolish / "input.cj").write_text("var a = 0\n", encoding="utf-8")
    plain = suite / "array"
    plain.mkdir()
    for name in ("a.cj", "b.cj", "c.cj"):
        (plain / name).write_text("// EXEC: x\n", encoding="utf-8")

    # 1 .info vs 4 .cj -> the larger count, so the .cj cases are not hidden.
    assert count_cases([suite], repo) == 4


def test_count_cases_takes_the_larger_of_info_and_cj(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    case_dir = repo / "testsuites" / "HLT" / "Tools" / "cjfmt" / "blank_line"
    (case_dir / "formated").mkdir(parents=True)
    (case_dir / "a.info").write_text("// EXEC: x\n", encoding="utf-8")
    (case_dir / "b.info").write_text("// EXEC: x\n", encoding="utf-8")
    for name in ("formated/a.cj", "formated/b.cj", "formated/c.cj"):
        (case_dir / name).write_text("var a = 0\n", encoding="utf-8")

    # 2 .info, 3 .cj -> 3, an overcount of the real 2 cases but never an undercount.
    assert count_cases([case_dir], repo) == 3


def test_count_cases_counts_plain_cj_cases(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    case_dir = repo / "testsuites" / "LLT" / "compiler" / "tuple"
    case_dir.mkdir(parents=True)
    for name in ("a.cj", "b.cj", "c.cj"):
        (case_dir / name).write_text("// EXEC: x\n", encoding="utf-8")

    assert count_cases([case_dir], repo) == 3
    # A lone file counts as one case however it is named.
    assert count_cases([case_dir / "a.cj"], repo) == 1


def test_suite_of_infers_from_the_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    llt = repo / "testsuites" / "LLT" / "compiler" / "a.cj"
    hlt = repo / "testsuites" / "HLT" / "compiler" / "b.cj"
    conf = repo / "Conformance" / "Compiler" / "testsuite" / "src" / "test_x.cj"
    for path in (llt, hlt, conf):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    assert suite_of(llt, repo) == "llt"
    assert suite_of(hlt, repo) == "hlt"
    assert suite_of(conf, repo) == "conformance"
    assert suite_of(Path("testsuites/LLT/compiler/a.cj"), repo) == "llt"
    assert suite_of(repo / "elsewhere.cj", repo) is None


def test_group_cases_mixes_suites_and_rejects_unknown(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    llt = repo / "testsuites" / "LLT" / "a.cj"
    hlt = repo / "testsuites" / "HLT" / "b.cj"
    stray = repo / "loose.cj"
    for path in (llt, hlt, stray):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    grouped = group_cases([llt, hlt], repo)
    assert grouped == {"llt": [llt], "hlt": [hlt]}

    with pytest.raises(BuildError, match="cannot tell which suite"):
        group_cases([llt, stray], repo)


def test_run_mapple_lists_files_through_the_selection_list_when_dirs_are_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generated --test_list filters discovery, so files must go in it too.

    Passing files positionally next to a --test_list would silently narrow the
    run: the framework only considers what the list names.
    """
    cfg = _fake_sdk(tmp_path)
    repo = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test"
    framework = tmp_path / "workspace" / "cangjie_test_checkouts" / "cangjie_test_framework"
    (repo / "testsuites" / "LLT" / "configs" / "cjnative").mkdir(parents=True)
    (repo / "testsuites" / "LLT" / "configs" / "cjnative" / "cjnative_test.cfg").write_text(
        "", encoding="utf-8"
    )
    (repo / "testsuites" / "LLT" / "cjnative_testlist").write_text("", encoding="utf-8")
    one = repo / "testsuites" / "LLT" / "compiler" / "a.cj"
    other_dir = repo / "testsuites" / "LLT" / "runtime"
    one.parent.mkdir(parents=True)
    other_dir.mkdir(parents=True)
    one.write_text("// EXEC: x\n", encoding="utf-8")
    (other_dir / "b.cj").write_text("// EXEC: x\n", encoding="utf-8")
    framework.mkdir(parents=True)
    (framework / "main.py").write_text("", encoding="utf-8")

    seen: list[list[str]] = []

    def fake_run(cmd: list[object], **kwargs: object) -> int:
        seen.append([str(p) for p in cmd])
        for arg in cmd:
            text = str(arg)
            if text.startswith("--json_output="):
                Path(text.split("=", 1)[1]).write_text("[]", encoding="utf-8")
        return 0

    monkeypatch.setattr("cangjie_build.stages.test.run_cmd", fake_run)
    run_mapple(
        cfg,
        repo,
        framework,
        tmp_path / "work",
        suite="llt",
        test_list=None,
        cases=[one, other_dir],
        jobs=2,
        base_timeout=30,
    )

    argv = seen[0]
    list_args = [a for a in argv if a.startswith("--test_list=")]
    assert len(list_args) == 1, argv
    body = Path(list_args[0].split("=", 1)[1]).read_text(encoding="utf-8")
    # Both the file's directory and the requested directory must be listed.
    assert "compiler/" in body, body
    assert "runtime/" in body, body


def test_toolchain_key_changes_when_a_pinned_input_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The key must follow the pins, so a rebuild never reuses a stale blob."""
    from cangjie_build.toolchain import mingw, toolchain_cache_key

    before = toolchain_cache_key("windows-x64")
    monkeypatch.setattr(mingw, "OPENSSL_VERSION", "9.9.9")
    after = toolchain_cache_key("windows-x64")
    assert before != after
    assert "openssl9.9.9" in after


def test_toolchain_key_covers_the_llvm_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An earlier hand-maintained key omitted the LLVM commit entirely."""
    from cangjie_build.toolchain import mingw, toolchain_cache_key

    before = toolchain_cache_key("windows-x64")
    monkeypatch.setattr(mingw, "LLVM_PROJECT_COMMIT", "f" * 40)
    after = toolchain_cache_key("windows-x64")
    assert before != after
    assert "llvmffffffffffff" in after


def test_toolchain_key_rejects_an_unknown_target() -> None:
    from cangjie_build.errors import ConfigError
    from cangjie_build.toolchain import toolchain_cache_key

    with pytest.raises(ConfigError, match="no toolchain is built"):
        toolchain_cache_key("macos-arm64")


def test_toolchain_key_covers_the_mingw_w64_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """mingw-w64 was cloned at branch HEAD, so its commit must be pinned and keyed."""
    from cangjie_build.toolchain import mingw, toolchain_cache_key

    before = toolchain_cache_key("windows-x64")
    monkeypatch.setattr(mingw, "MINGW_W64_COMMIT", "a" * 40)
    after = toolchain_cache_key("windows-x64")
    assert before != after
    assert "mingwaaaaaaaaaaaa" in after


def test_mingw_w64_is_pinned_rather_than_tracking_a_branch() -> None:
    from cangjie_build.toolchain import mingw

    # A branch clone would make the artifact depend on upstream's current HEAD.
    assert len(mingw.MINGW_W64_COMMIT) == 40
    assert all(c in "0123456789abcdef" for c in mingw.MINGW_W64_COMMIT)


def test_system_deps_include_the_python_headers_lldb_needs() -> None:
    """lldb's FindPythonAndSwig fails without Python3_INCLUDE_DIRS."""
    from cangjie_build.toolchain import system_deps

    assert "python3-dev" in system_deps.APT_PACKAGES
    # swig must stay: the same CMake module looks for it.
    assert "swig" in system_deps.APT_PACKAGES
