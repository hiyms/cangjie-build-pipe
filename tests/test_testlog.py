from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any

import pytest

from cangjie_build import testlog
from cangjie_build.errors import BuildError


def _section(*cases: dict[str, Any], total: int | None = None, **counts: int) -> dict[str, Any]:
    return {
        "name": "LLT /w",
        "total": total if total is not None else len(cases),
        **counts,
        "tests": list(cases),
    }


def _case(name: str, result: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "result": result, **extra}


def _write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _render(tmp_path: Path, sections: list[dict[str, Any]]) -> str:
    results = _write(tmp_path / "results.json", sections)
    return testlog.render(
        suite="llt",
        target="linux-x64",
        label="42",
        results=results,
        sections=testlog.sections_of(results),
    )


def test_a_passing_case_gets_one_line(tmp_path: Path) -> None:
    """The log stays readable when a suite runs fourteen thousand passing cases."""
    text = _render(tmp_path, [_section(_case("a/b.cj", "PASS"), _case("c/d.cj", "PASS"))])

    assert "PASS    a/b.cj" in text
    assert "PASS    c/d.cj" in text
    assert "TestCase" not in text


def test_a_failing_case_shows_every_command_and_its_output(tmp_path: Path) -> None:
    failing = _case(
        "a/b.cj",
        "FAIL",
        commands=[{"cmd": "cjc b.cj"}, {"cmd": "b.out"}],
        output=[
            {"cmd": "cjc b.cj", "return_code": 0, "stdout": "", "stderr": "warning: unused"},
            {"cmd": "b.out", "return_code": 1, "stdout": "boom\nsecond line", "stderr": ""},
        ],
    )

    text = _render(tmp_path, [_section(failing)])

    assert "TestCase FAIL:" in text
    assert "cjc b.cj" in text
    assert "Return code    : 1" in text
    assert "boom" in text
    # Continuation lines line up under their own label so a multi-line diagnostic
    # stays readable.
    assert "\n                 second line" in text


def test_a_case_with_no_verdict_is_counted_but_not_listed(tmp_path: Path) -> None:
    """LLT emits thousands of helper files with no EXEC header; naming them buries the failures."""
    text = _render(
        tmp_path,
        [
            _section(
                _case("ran.cj", "PASS"),
                _case("helper.cj", "UNRESOLVED"),
                total=2,
                PASS=1,
                UNRESOLVED=1,
            )
        ],
    )

    assert "helper.cj" not in text
    assert "Total: 2, passed: 1, failed: 0, not run: 1" in text


def test_the_tail_names_the_failures(tmp_path: Path) -> None:
    text = _render(
        tmp_path,
        [
            _section(
                _case("ok.cj", "PASS"),
                _case("bad.cj", "FAIL"),
                total=2,
                PASS=1,
                FAIL=1,
            )
        ],
    )

    tail = text.split("-" * 78)[-1]
    assert "Total: 2, passed: 1, failed: 1" in tail
    assert "FAIL (1):" in tail
    assert "  bad.cj" in tail


def test_a_preparation_failure_is_printed_as_a_note(tmp_path: Path) -> None:
    """Maple writes a bare reason instead of a command list when it cannot even start."""
    broken = _case("pkg/a.cj", "FAIL", commands=[], output="Test case preparation failed, no cfg")

    text = _render(tmp_path, [_section(broken, total=1, FAIL=1)])

    assert "Test case preparation failed, no cfg" in text


def test_a_suite_log_is_written_per_suite(tmp_path: Path) -> None:
    root = tmp_path / "test-results"
    _write(root / "llt" / "results.json", [_section(_case("a.cj", "PASS"))])
    _write(root / "hlt" / "results.json", [_section(_case("b.cj", "PASS"))])

    written = testlog.write_suite_logs(root, tmp_path / "out", target="linux-x64", label="7")

    assert [p.name for p in written] == [
        "test-hlt-linux-x64-7.log",
        "test-llt-linux-x64-7.log",
    ]


def test_a_suite_that_did_not_run_writes_no_log(tmp_path: Path) -> None:
    root = tmp_path / "test-results"
    _write(root / "llt" / "results.json", [_section(_case("a.cj", "PASS"))])

    written = testlog.write_suite_logs(root, tmp_path / "out", target="linux-x64", label="7")

    assert [p.name for p in written] == ["test-llt-linux-x64-7.log"]


def test_the_conformance_report_becomes_a_log(tmp_path: Path) -> None:
    root = tmp_path / "test-results"
    _write(
        root / "conformance" / "results.log.json",
        [
            {"cjc": "/sdk/bin/cjc"},
            {"customs": {}},
            {"test_path": "/t/ok.cj", "result": "PASSED", "compile_log": "", "execute_log": ""},
            {
                "test_path": "/t/bad.cj",
                "result": "FAILED",
                "compile_log": "",
                "execute_log": "expected 1, got 2",
            },
            {"total_time": 1.0},
        ],
    )

    written = testlog.collect(root, tmp_path / "out", target="linux-x64", label="7")
    text = (tmp_path / "out" / "test-conformance-linux-x64-7.log").read_text(encoding="utf-8")

    assert [p.name for p in written if p.suffix == ".log"] == ["test-conformance-linux-x64-7.log"]
    assert "PASSED  /t/ok.cj" in text
    assert "TestCase FAILED:" in text
    assert "expected 1, got 2" in text
    assert "Total: 2, passed: 1, failed: 1" in text


def test_the_archive_holds_the_reports_and_the_per_case_logs(tmp_path: Path) -> None:
    root = tmp_path / "test-results"
    _write(root / "llt" / "results.json", [_section(_case("a.cj", "PASS"))])
    log = root / "llt" / "log" / "a_cj_1.log"
    log.parent.mkdir(parents=True)
    log.write_text("frame\n", encoding="utf-8")

    out = testlog.write_verbose_archive(root, tmp_path / "test-verbose-linux-x64-7.tar.gz")

    assert out is not None
    with tarfile.open(out, "r:gz") as archive:
        names = sorted(archive.getnames())
        assert names == ["llt-log/a_cj_1.log", "llt-results.json"]
        member = archive.extractfile("llt-log/a_cj_1.log")
        assert member is not None
        assert member.read() == b"frame\n"


def test_the_archive_keeps_the_work_directories_out(tmp_path: Path) -> None:
    """A conformance case leaves ~2 MB of .cjo/.a next to its report."""
    root = tmp_path / "test-results"
    _write(
        root / "conformance" / "results.log.json", [{"test_path": "/t/a.cj", "result": "PASSED"}]
    )
    binary = root / "conformance" / "run" / "a" / "a.out"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"\x7fELF")

    out = testlog.write_verbose_archive(root, tmp_path / "v.tar.gz")

    assert out is not None
    with tarfile.open(out, "r:gz") as archive:
        assert archive.getnames() == ["conformance-results.log.json"]


def test_an_empty_results_tree_yields_nothing(tmp_path: Path) -> None:
    root = tmp_path / "test-results"
    root.mkdir()

    assert testlog.collect(root, tmp_path / "out", target="linux-x64", label="7") == []


def test_a_missing_results_tree_yields_nothing(tmp_path: Path) -> None:
    """The suites never started; the workflow reports that separately."""
    assert (
        testlog.collect(tmp_path / "absent", tmp_path / "out", target="linux-x64", label="7") == []
    )


def test_a_label_that_would_reshape_the_file_name_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "test-results"
    _write(root / "llt" / "results.json", [_section(_case("a.cj", "PASS"))])

    with pytest.raises(BuildError, match="not usable in a file name"):
        testlog.write_suite_logs(root, tmp_path / "out", target="linux-x64", label="../../etc")


def test_an_unreadable_report_is_an_error(tmp_path: Path) -> None:
    results = tmp_path / "results.json"
    results.write_text("{ not json", encoding="utf-8")

    with pytest.raises(BuildError, match="not readable JSON"):
        testlog.sections_of(results)


def test_the_verdict_tail_and_the_listing_agree(tmp_path: Path) -> None:
    """A reader must not see a failure count that the list below contradicts."""
    text = _render(
        tmp_path,
        [
            _section(
                _case("ok.cj", "PASS"),
                _case("bad1.cj", "FAIL"),
                _case("bad2.cj", "FAIL"),
                _case("skip.cj", "UNRESOLVED"),
                total=4,
                PASS=1,
                FAIL=2,
                UNRESOLVED=1,
            )
        ],
    )

    tail = text.split("-" * 78)[-1]
    assert "Total: 4, passed: 1, failed: 2, not run: 1" in tail
    assert "FAIL (2):" in tail
    assert "  bad1.cj" in tail and "  bad2.cj" in tail
    assert "skip.cj" not in tail


def test_a_label_of_two_dots_stays_inside_the_output_directory(tmp_path: Path) -> None:
    """The label lands in a file name, so it must not be able to walk out of it."""
    root = tmp_path / "test-results"
    _write(root / "llt" / "results.json", [_section(_case("a.cj", "PASS"))])
    out = tmp_path / "out"

    written = testlog.write_suite_logs(root, out, target="linux-x64", label="..")

    assert written and all(p.parent == out for p in written)


def test_the_archive_name_is_checked_too(tmp_path: Path) -> None:
    """The archive is named from the same label, so it gets the same guard."""
    root = tmp_path / "test-results"
    _write(root / "llt" / "results.json", [_section(_case("a.cj", "PASS"))])

    with pytest.raises(BuildError, match="not usable in a file name"):
        testlog.collect(root, tmp_path / "out", target="linux-x64", label="../../etc")
