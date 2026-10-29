from pathlib import Path

import pytest

from cangjie_build import summary


def test_render_lists_each_suite() -> None:
    reports = [
        summary.SuiteReport("llt", {"total": 5, "PASS": 5}),
        summary.SuiteReport("hlt", {"total": 1, "PASS": 0, "FAIL": 1}),
    ]
    md = summary.render(reports)
    assert "| LLT | 5 | 5 | 0 |" in md
    assert "| HLT | 1 | 0 | 1 |" in md
    assert "6 case(s), 1 failed" in md


def test_unresolved_and_not_run_count_as_failed() -> None:
    report = summary.SuiteReport("llt", {"total": 4, "PASS": 2, "UNRESOLVED": 1, "NOT_RUN": 1})
    assert report.failed == 2


def test_write_is_a_noop_without_the_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert summary.write([summary.SuiteReport("llt", {"total": 1, "PASS": 1})]) is None


def test_write_appends_to_the_summary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "summary.md"
    target.write_text("existing\n")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    summary.write([summary.SuiteReport("llt", {"total": 2, "PASS": 2})])
    body = target.read_text()
    assert body.startswith("existing\n")
    assert "| LLT | 2 | 2 | 0 |" in body


def test_totals_are_read_from_a_suite_that_failed(tmp_path: Path) -> None:
    """Maple writes its json as it goes, so a failed suite still has counts."""
    from cangjie_build.stages.test import totals_if_any

    work = tmp_path / "llt"
    work.mkdir()
    (work / "results.json").write_text('[{"total": 3, "PASS": 1, "FAIL": 2}]', encoding="utf-8")
    assert totals_if_any(work) == {"total": 3, "PASS": 1, "FAIL": 2}


def test_missing_or_unreadable_results_yield_no_totals(tmp_path: Path) -> None:
    from cangjie_build.stages.test import totals_if_any

    work = tmp_path / "llt"
    work.mkdir()
    assert totals_if_any(work) is None

    (work / "results.json").write_text("not json", encoding="utf-8")
    assert totals_if_any(work) is None
