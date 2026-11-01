from __future__ import annotations

import json
import tarfile
from pathlib import Path
from typing import Any, cast

from cangjie_build.errors import BuildError
from cangjie_build.summary import FAILED_KEYS, NOT_RUN_KEYS, PASSED_KEYS

# The status vocabulary is shared with the run summary, so the two never disagree
# about how many cases of one run failed.
PASSING: frozenset[str] = frozenset(PASSED_KEYS)
NO_VERDICT: frozenset[str] = frozenset(NOT_RUN_KEYS)

# Suite directory names whose Maple ``results.json`` this module renders. The
# conformance harness writes a differently shaped report and is rendered by
# ``_conformance_log`` instead.
MAPLE_SUITES: tuple[str, ...] = ("hlt", "llt")

_LABEL_WIDTH = 17
_TOTAL_KEYS: tuple[str, ...] = (
    "total",
    "PASS",
    "FAIL",
    "UNRESOLVED",
    "NOT_RUN",
    "UNSUPPORTED",
    "XFAIL",
    "XPASS",
)


def sections_of(results: Path) -> list[dict[str, Any]]:
    """Per-section records from one suite's ``results.json``."""
    try:
        data = cast(object, json.loads(results.read_text(encoding="utf-8")))
    except json.JSONDecodeError as exc:
        raise BuildError("test.artifacts", f"{results} is not readable JSON: {exc}") from exc
    if not isinstance(data, list):
        raise BuildError("test.artifacts", f"unexpected shape in {results}")
    return [
        cast("dict[str, Any]", entry)
        for entry in cast("list[object]", data)
        if isinstance(entry, dict)
    ]


def _cases(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for section in sections:
        tests = section.get("tests")
        if not isinstance(tests, list):
            continue
        cases.extend(
            cast("dict[str, Any]", entry)
            for entry in cast("list[object]", tests)
            if isinstance(entry, dict)
        )
    return cases


def _totals(sections: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for section in sections:
        for key in _TOTAL_KEYS:
            value = section.get(key)
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return totals


def _status(case: dict[str, Any]) -> str:
    raw = case.get("result")
    return raw if isinstance(raw, str) else "UNKNOWN"


def _name(case: dict[str, Any]) -> str:
    raw = case.get("name")
    return raw if isinstance(raw, str) else "?"


def _label(text: str, value: str) -> str:
    return f"{text:<{_LABEL_WIDTH - 2}}: {value}"


def _stream(value: object) -> str:
    """A captured stream, its continuation lines aligned under the label."""
    text = value if isinstance(value, str) else ""
    if not text:
        return ""
    continuation = " " * _LABEL_WIDTH
    return ("\n" + continuation).join(text.rstrip("\n").split("\n"))


def _commands(case: dict[str, Any]) -> list[str]:
    raw = case.get("commands")
    if not isinstance(raw, list):
        return [_label("CMD list", "")]
    lines = [_label("CMD list", "")]
    for entry in cast("list[object]", raw):
        if isinstance(entry, dict):
            lines.append("  " + str(cast("dict[str, Any]", entry).get("cmd", "")))
    return lines


def _ran(case: dict[str, Any]) -> list[str]:
    """What each command of the case printed, with its return code.

    ``commands`` and ``output`` are parallel lists, but ``output`` is the one
    carrying the return code and the captured streams. It is normally a list of
    records; Maple writes a bare string there instead when the case could not
    even start, and a tuple when nothing has run yet, so those shapes are printed
    as a note rather than dropped.
    """
    raw = case.get("output")
    if raw is None:
        return []
    if isinstance(raw, str):
        return ["--------", str(raw), "--------"] if raw else []
    if not isinstance(raw, list):
        return ["--------", str(raw), "--------"]
    lines: list[str] = []
    for entry in cast("list[object]", raw):
        if not isinstance(entry, dict):
            lines.append("--------")
            lines.append(str(entry))
            continue
        record = cast("dict[str, Any]", entry)
        lines.append("--------")
        lines.append(_label("CMD", str(record.get("cmd", ""))))
        lines.append(_label("Return code", str(record.get("return_code", ""))))
        lines.append(_label("Stdout", _stream(record.get("stdout"))))
        lines.append(_label("Stderr", _stream(record.get("stderr"))))
    if lines:
        lines.append("--------")
    return lines


def _block(case: dict[str, Any]) -> list[str]:
    return [
        "-" * 78,
        f"TestCase {_status(case)}:",
        _label("Name", _name(case)),
        *_commands(case),
        *_ran(case),
    ]


def _non_passing(cases: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Names of the cases that ran and did not pass, grouped by status.

    A case with no verdict never ran, and LLT produces thousands of them for
    helper files that carry no ``EXEC`` header; naming each one would bury the
    failures the reader is looking for. They are counted in the tallies instead.
    """
    grouped: dict[str, list[str]] = {}
    for case in cases:
        status = _status(case)
        if status in PASSING or status in NO_VERDICT:
            continue
        grouped.setdefault(status, []).append(_name(case))
    return grouped


def _verdict(totals: dict[str, int], cases: list[dict[str, Any]]) -> list[str]:
    passed = sum(totals.get(key, 0) for key in PASSED_KEYS)
    failed = sum(totals.get(key, 0) for key in FAILED_KEYS)
    not_run = sum(totals.get(key, 0) for key in NOT_RUN_KEYS)
    head = f"Total: {totals.get('total', len(cases))}, passed: {passed}, failed: {failed}"
    if not_run:
        head += f", not run: {not_run}"
    lines = ["-" * 78, head]
    grouped = _non_passing(cases)
    if not grouped:
        return lines
    lines.append("")
    for status, names in grouped.items():
        lines.append(f"{status} ({len(names)}):")
        lines.extend(f"  {name}" for name in names)
    return lines


def render(*, suite: str, target: str, label: str, results: Path, sections: list[Any]) -> str:
    """One suite's log: a line per case that ran, the output of every failure.

    The shape follows what Maple prints today so the file reads the same way as a
    local run: ``PASS`` plus the case name for everything that reached a verdict,
    the full command output for anything that failed, then the tallies and the
    list of cases that did not pass.
    """
    records = [cast("dict[str, Any]", section) for section in sections if isinstance(section, dict)]
    cases = _cases(records)
    lines = [
        _label("suite", suite),
        _label("target", target),
        _label("run", label),
        _label("results", str(results)),
        "",
    ]
    for case in cases:
        status = _status(case)
        if status in PASSING:
            lines.append(f"{status:<6}  {_name(case)}")
        elif status in NO_VERDICT:
            continue
        else:
            lines.extend(_block(case))
    lines.extend(_verdict(_totals(records), cases))
    return "\n".join(lines) + "\n"


def _check_label(label: str) -> str:
    """Reject a label that would change the shape of the file name."""
    if not label or label.strip() != label or "/" in label or "\\" in label:
        raise BuildError("test.artifacts", f"label {label!r} is not usable in a file name")
    return label


def write_suite_logs(
    results_root: Path, out_dir: Path, *, target: str, label: str, prefix: str = "test"
) -> list[Path]:
    """Render one log per Maple suite that produced a ``results.json``."""
    written: list[Path] = []
    if not results_root.is_dir():
        return written
    for suite in MAPLE_SUITES:
        suite_dir = results_root / suite
        results = suite_dir / "results.json"
        if not results.is_file():
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{prefix}-{suite}-{target}-{_check_label(label)}.log"
        out.write_text(
            render(
                suite=suite,
                target=target,
                label=label,
                results=results,
                sections=sections_of(results),
            ),
            encoding="utf-8",
        )
        written.append(out)
    return written


def _archive_entries(results_root: Path) -> list[tuple[str, Path]]:
    """The report files to keep, laid out as ``<suite>-results.json`` and ``<suite>-log/``."""
    entries: list[tuple[str, Path]] = []
    for suite_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        suite = suite_dir.name
        for name in ("results.log", "results.log.json", "results.json"):
            candidate = suite_dir / name
            if candidate.is_file():
                entries.append((f"{suite}-{name}", candidate))
        log_dir = suite_dir / "log"
        if log_dir.is_dir():
            for entry in sorted(path for path in log_dir.iterdir() if path.is_file()):
                entries.append((f"{suite}-log/{entry.name}", entry))
    return entries


def write_verbose_archive(results_root: Path, out: Path) -> Path | None:
    """Pack every suite's report and per-case log into one archive.

    The run directories next to them hold compiled binaries and object files
    (measured ~2 MB for a single conformance case), so only the reports and the
    framework's own logs are packed; the rest stays on the VM.
    """
    if not results_root.is_dir():
        return None
    entries = _archive_entries(results_root)
    if not entries:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz", compresslevel=6) as archive:
        for arcname, path in entries:
            info = archive.gettarinfo(str(path), arcname=arcname)
            # Normalised so the archive records no runner account and no clock.
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            with path.open("rb") as handle:
                archive.addfile(info, handle)  # pyright: ignore[reportArgumentType]
    return out


def _conformance_log(results: Path, *, target: str, label: str) -> str:
    """A readable log for the conformance harness's ``results.log.json``.

    Its entries carry the case path and verdict but keep their output in
    ``compile_log``/``execute_log``, so the same block layout is reused from the
    Maple side over a differently shaped record.
    """
    sections = sections_of(results)
    cases = [
        cast("dict[str, Any]", entry)
        for entry in cast("list[object]", sections)
        if "test_path" in cast("dict[str, Any]", entry)
    ]
    totals: dict[str, int] = {}
    for case in cases:
        status = _status(case)
        totals[status] = totals.get(status, 0) + 1
    lines = [
        _label("suite", "conformance"),
        _label("target", target),
        _label("run", label),
        _label("results", str(results)),
        "",
    ]
    for case in cases:
        status = _status(case)
        if status in PASSING:
            lines.append(f"{status:<6}  {_conformance_name(case)}")
            continue
        if status in NO_VERDICT:
            continue
        lines.extend(_conformance_block(case))
    grouped: dict[str, list[str]] = {}
    for case in cases:
        status = _status(case)
        if status in PASSING or status in NO_VERDICT:
            continue
        grouped.setdefault(status, []).append(_conformance_name(case))
    passed = sum(totals.get(key, 0) for key in PASSED_KEYS)
    failed = sum(totals.get(key, 0) for key in FAILED_KEYS)
    not_run = sum(totals.get(key, 0) for key in NOT_RUN_KEYS)
    tail = f"Total: {len(cases)}, passed: {passed}, failed: {failed}"
    if not_run:
        tail += f", not run: {not_run}"
    lines.append("-" * 78)
    lines.append(tail)
    for status, names in grouped.items():
        lines.append("")
        lines.append(f"{status} ({len(names)}):")
        lines.extend(f"  {name}" for name in names)
    return "\n".join(lines) + "\n"


def _conformance_name(case: dict[str, Any]) -> str:
    raw = case.get("test_path")
    return raw if isinstance(raw, str) else "?"


def _conformance_block(case: dict[str, Any]) -> list[str]:
    lines = ["-" * 78, f"TestCase {_status(case)}:", _label("Name", _conformance_name(case))]
    for key in ("compile_log", "execute_log"):
        text = case.get(key)
        body = text if isinstance(text, str) else ""
        if not body.strip():
            continue
        lines.append(_label(key, ""))
        lines.extend("  " + line for line in body.rstrip("\n").split("\n"))
    return lines


def collect(results_root: Path, out_dir: Path, *, target: str, label: str) -> list[Path]:
    """Everything the test workflow uploads, in the order it is created.

    One log per suite that produced a verdict file, plus one archive holding every
    raw report and per-case log. A suite without a report is absent rather than
    empty, so the workflow can upload ``test-<suite>-*.log`` per file without
    having to know which suites ran.
    """
    written = write_suite_logs(results_root, out_dir, target=target, label=label)
    conformance = results_root / "conformance" / "results.log.json"
    if conformance.is_file():
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"test-conformance-{target}-{_check_label(label)}.log"
        out.write_text(_conformance_log(conformance, target=target, label=label), encoding="utf-8")
        written.append(out)
    archive = write_verbose_archive(
        results_root, out_dir / f"test-verbose-{target}-{_check_label(label)}.tar.gz"
    )
    if archive is not None:
        written.append(archive)
    return written
