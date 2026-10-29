from __future__ import annotations

import re
from pathlib import Path

from cangjie_guide.version import FALLBACK_VERSION, SEMVER_PATTERN, normalise

ROOT = Path(__file__).resolve().parents[2]


def test_matches_the_harness_implementation() -> None:
    """guide/ is a separate project, so the rule is copied; pin it to the source.

    If the harness changes its normalisation, artifact names would differ from
    the ones this tool predicts, and the overwrite guard would check the wrong
    name without saying so.
    """
    source = (ROOT / "src/cangjie_build/config.py").read_text(encoding="utf-8")
    harness = re.search(r"_SEMVER_RE = re\.compile\(r\"([^\"]+)\"\)", source)
    assert harness is not None, "cannot find _SEMVER_RE in the harness"
    fallback = re.search(r'_FALLBACK_VERSION = "([^"]+)"', source)
    assert fallback is not None, "cannot find _FALLBACK_VERSION in the harness"

    assert SEMVER_PATTERN.pattern == harness.group(1)
    assert fallback.group(1) == FALLBACK_VERSION


def test_semver_passes_through() -> None:
    assert normalise("1.2.3") == "1.2.3"
    assert normalise("v1.2.3") == "1.2.3"
    assert normalise("1.2.3-rc.1+build.42") == "1.2.3-rc.1+build.42"


def test_branch_names_collapse_to_the_fallback() -> None:
    """tag=main and tag=dev both name the same artifact."""
    assert normalise("main") == FALLBACK_VERSION
    assert normalise("dev") == FALLBACK_VERSION
    assert normalise("") == FALLBACK_VERSION
