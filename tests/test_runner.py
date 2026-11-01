from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cangjie_build.errors import BuildError
from cangjie_build.runner import run, run_capture


def test_run_zero_exit() -> None:
    rc = run([sys.executable, "-c", "print('ok')"], stage="t.ok", echo=False)
    assert rc == 0


def test_run_non_zero_raises_build_error() -> None:
    with pytest.raises(BuildError) as ei:
        run([sys.executable, "-c", "import sys; sys.exit(7)"], stage="t.fail", echo=False)
    assert ei.value.returncode == 7
    assert ei.value.stage == "t.fail"


def test_run_check_false_returns_code() -> None:
    rc = run(
        [sys.executable, "-c", "import sys; sys.exit(2)"],
        stage="t.no_check",
        check=False,
        echo=False,
    )
    assert rc == 2


def test_run_passes_env_overlay(tmp_path: Path) -> None:
    out = tmp_path / "out.txt"
    script = f"import os, pathlib;pathlib.Path(r'{out}').write_text(os.environ.get('FOO', ''))"
    run(
        [sys.executable, "-c", script],
        stage="t.env",
        env_overlay={"FOO": "bar123"},
        echo=False,
    )
    assert out.read_text() == "bar123"


def test_run_uses_cwd(tmp_path: Path) -> None:
    out = tmp_path / "where.txt"
    script = f"import os, pathlib; pathlib.Path(r'{out}').write_text(os.getcwd())"
    sub = tmp_path / "sub"
    sub.mkdir()
    run([sys.executable, "-c", script], cwd=sub, stage="t.cwd", echo=False)
    # Some platforms return long paths; compare resolved real paths.
    assert Path(out.read_text()).resolve() == sub.resolve()


def test_run_rejects_empty_command() -> None:
    with pytest.raises(BuildError):
        run([], stage="t.empty", echo=False)


def test_run_capture_returns_the_combined_output() -> None:
    rc, out = run_capture(
        [sys.executable, "-c", "print('hello'); print('world')"], stage="t.capture", echo=False
    )

    assert rc == 0
    assert out == "hello\nworld\n"


def test_run_capture_keeps_nothing_when_capture_is_off() -> None:
    rc, out = run_capture(
        [sys.executable, "-c", "print('hello')"],
        stage="t.stream",
        echo=False,
        capture=False,
    )

    assert rc == 0
    assert out == ""


def test_a_failing_child_is_killed_when_the_reader_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupted read must not leave the child running."""
    pid_file = tmp_path / "pid"
    monkeypatch.setattr(
        "cangjie_build.runner._log",
        _RaisingLogger(pid_file),
    )

    with pytest.raises(RuntimeError, match="reader interrupted"):
        run_capture(
            [
                sys.executable,
                "-c",
                "import os, pathlib, sys, time; "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "print('started', flush=True); time.sleep(60)",
                str(pid_file),
            ],
            stage="t.interrupt",
            echo=False,
        )

    pid = int(pid_file.read_text())
    _assert_process_gone(pid)


def _assert_process_gone(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


class _RaisingLogger:
    """Stands in for the module logger and fails on the first record."""

    def __init__(self, pid_file: Path) -> None:
        self._pid_file = pid_file

    def info(self, *_args: object) -> None:
        raise RuntimeError("reader interrupted")
