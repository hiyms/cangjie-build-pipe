from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SuiteReport:
    """Tallies for one suite, as the harness wrote them."""

    suite: str
    totals: dict[str, int] = field(default_factory=dict[str, int])

    @property
    def total(self) -> int:
        return self.totals.get("total", 0)

    @property
    def passed(self) -> int:
        return self.totals.get("PASS", 0)

    @property
    def failed(self) -> int:
        # Maple reports FAIL separately from UNRESOLVED and NOT_RUN; all three
        # mean the case did not pass, so they are shown together.
        return (
            self.totals.get("FAIL", 0)
            + self.totals.get("UNRESOLVED", 0)
            + self.totals.get("NOT_RUN", 0)
        )


def _table_row(report: SuiteReport) -> str:
    return f"| {report.suite.upper()} | {report.total} | {report.passed} | {report.failed} |"


def render(reports: list[SuiteReport]) -> str:
    """Markdown for the run summary page.

    A suite that ran is listed even when it failed; a suite that was not selected
    is absent rather than shown as zero, because "0 of 0" reads like a pass.
    """
    lines = [
        "## Test results",
        "",
        "| suite | total | passed | failed |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(_table_row(report) for report in reports)
    total = sum(r.total for r in reports)
    failed = sum(r.failed for r in reports)
    lines += ["", f"**{total} case(s), {failed} failed.**"]
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
