from __future__ import annotations

import re

from typer.testing import CliRunner

from cangjie_build.cli import app


def _run(*args: str) -> tuple[int, str]:
    """Run the CLI and return the exit code plus the output with wrapping undone.

    Rich wraps error panels to the terminal width, which splits the messages
    these tests match on.
    """
    runner = CliRunner()
    result = runner.invoke(app, list(args), catch_exceptions=False, color=False)
    plain = re.sub(r"[\u2500-\u257f]", " ", result.output)
    return result.exit_code, " ".join(plain.split())


def test_test_list_is_refused_with_conformance_even_when_spelled_out() -> None:
    """--test-list names HLT/LLT cases; conformance selects by --tests."""
    code, out = _run("test", "--suite", "conformance", "--test-list", "cases.txt")

    assert code != 0
    assert "use --tests for conformance" in out


def test_test_list_is_refused_with_a_suite_list() -> None:
    code, out = _run("test", "--suite", "conformance", "--suite", "llt", "--test-list", "x.txt")

    assert code != 0
    assert "use --tests for conformance" in out


def test_unknown_suite_is_refused() -> None:
    code, out = _run("test", "--suite", "smoke")

    assert code != 0
    assert "unknown suite" in out


def test_changed_in_and_case_are_mutually_exclusive() -> None:
    code, out = _run("test", "--changed-in", "HEAD", "--case", "testsuites/LLT/a.cj")

    assert code != 0
    assert "changed-in picks the cases itself" in out


def test_toolchain_cache_key_follows_the_global_target() -> None:
    """Both spellings must name the same toolchain."""
    global_code, global_out = _run("--target", "linux-x64", "toolchain-cache-key")
    local_code, local_out = _run("toolchain-cache-key", "--target", "linux-x64")

    assert global_code == 0 and local_code == 0
    assert global_out.strip() == local_out.strip()
    assert global_out.strip().startswith("staticlibs-")


def test_toolchain_cache_key_defaults_to_the_selected_target() -> None:
    code, out = _run("--target", "windows-x64", "toolchain-cache-key")

    assert code == 0
    assert out.strip().startswith("mingw-")


def test_print_version_follows_the_explicit_option() -> None:
    code, out = _run("--cangjie-version", "1.2.3", "print-version")

    assert code == 0
    assert out.strip() == "1.2.3"


def test_print_version_normalises_a_v_prefixed_input() -> None:
    code, out = _run("--cangjie-version", "v0.59.6", "print-version")

    assert code == 0
    assert out.strip() == "0.59.6"


def test_print_version_falls_back_for_a_branch_name() -> None:
    code, out = _run("--cangjie-version", "main", "print-version")

    assert code == 0
    assert out.strip() == "0.0.0-dev"


def test_a_bad_target_is_reported_as_a_usage_error() -> None:
    code, out = _run("--target", "aix-power", "print-version")

    assert code == 2
    assert "is not one of" in out
