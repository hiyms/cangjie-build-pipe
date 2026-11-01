from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# One vocabulary for both frameworks, so the run summary and the per-case log
# cannot disagree about the same run. Maple writes PASS/FAIL/XFAIL/XPASS/
# UNRESOLVED/NOT_RUN/UNSUPPORTED; the conformance harness writes PASSED/FAILED/
# ERRORED/INCOMPLETE/SKIPPED.
PASSED_KEYS: tuple[str, ...] = ("PASS", "PASSED", "XFAIL", "XPASS")

# Cases that ran and did not pass. Maple puts FAIL in its own UNSUCCESSFUL
# bucket; the conformance harness separates FAILED from ERRORED and INCOMPLETE.
FAILED_KEYS: tuple[str, ...] = ("FAIL", "FAILED", "ERRORED", "INCOMPLETE")

# Cases the harness accounted for but never executed: a helper file with no
# EXEC header, a case whose conditions did not match, or a run mode that skipped
# them. None of these produced a verdict, so none of them is a failure.
NOT_RUN_KEYS: tuple[str, ...] = ("UNRESOLVED", "NOT_RUN", "UNSUPPORTED", "SKIPPED")


@dataclass
class SuiteReport:
    """Tallies for one suite, keyed by whatever status names the harness wrote."""

    suite: str
    totals: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def total(self) -> int:
        """Cases the harness accounted for.

        Maple states this itself, and its value already includes UNRESOLVED and
        NOT_RUN, so summing the statuses next to it would count those twice. The
        conformance file has no such entry and writes one status key per case,
        so there the statuses are the count.
        """
        if "total" in self.totals:
            return self.totals["total"]
        return sum(self.totals.values())

    @property
    def passed(self) -> int:
        return sum(self.totals.get(key, 0) for key in PASSED_KEYS)

    @property
    def failed(self) -> int:
        """Cases that reached a verdict and did not pass.

        UNRESOLVED, NOT_RUN, UNSUPPORTED and SKIPPED never ran the case, so
        counting them as failures would report a different number than the
        per-case log of the same run.
        """
        return sum(self.totals.get(key, 0) for key in FAILED_KEYS)

    @property
    def not_run(self) -> int:
        """Cases the harness accounted for but never ran."""
        return sum(self.totals.get(key, 0) for key in NOT_RUN_KEYS)

    @property
    def executed(self) -> int:
        """Cases that reached a verdict.

        Neither harness exits non-zero for a selection that matched nothing, so
        the caller has to tell a real run from an empty one. A case that never
        ran is not evidence that the selection was exercised.
        """
        return self.passed + self.failed


def _table_row(report: SuiteReport) -> str:
    return (
        f"| {report.suite.upper()} | {report.total} | {report.passed} "
        f"| {report.failed} | {report.not_run} |"
    )


def render(reports: list[SuiteReport]) -> str:
    """Markdown for the run summary page.

    A suite that ran is listed even when it failed; a suite that was not selected
    is absent rather than shown as zero, because "0 of 0" reads like a pass.
    """
    lines = [
        "## Test results",
        "",
        "| suite | total | passed | failed | not run |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(_table_row(report) for report in reports)
    total = sum(r.total for r in reports)
    failed = sum(r.failed for r in reports)
    not_run = sum(r.not_run for r in reports)
    lines += ["", f"**{total} case(s), {failed} failed, {not_run} not run.**"]
    if failed == 0 and total > 0:
        lines.append("")
        lines.append(
            "Passing means every selected case compiled and its assertions held. "
            "A case whose assertions are filtered out by its own conditions still "
            "counts as a pass here."
        )
    return "\n".join(lines) + "\n"


def write(reports: list[SuiteReport], path: Path | None = None) -> Path | None:
    """Append the summary to the run's summary file, if the runner provides one.

    Written directly rather than through a workflow step so the tallies come from
    the harness that produced them, not from a second parse of the same files.
    """
    if not reports:
        return None
    target = path or _summary_path()
    if target is None:
        return None
    with target.open("a", encoding="utf-8") as handle:
        handle.write(render(reports))
    return target


def _summary_path() -> Path | None:
    raw = os.environ.get("GITHUB_STEP_SUMMARY")
    if not raw:
        return None
    return Path(raw)
