from __future__ import annotations

import pytest

from cangjie_guide.constraints import SuiteSelection, check_test_selection
from cangjie_guide.errors import GuideError
from cangjie_guide.runs import find_sdk_artifact, sdk_archive_name
from cangjie_guide.schema import Artifact


def _sel(
    *,
    suites: tuple[str, ...] = ("llt",),
    cases: tuple[str, ...] = ("testsuites/LLT/a.cj",),
    paths: tuple[str, ...] = (),
    changed_in: str = "",
    sdk_artifact: str = "cangjie-linux-x64-0.0.0-dev",
    sdk_url: str = "",
    vm_size: str = "auto",
) -> SuiteSelection:
    return SuiteSelection(
        suites=suites,
        cases=cases,
        paths=paths,
        changed_in=changed_in,
        test_list="",
        sdk_artifact=sdk_artifact,
        sdk_url=sdk_url,
        vm_size=vm_size,
    )


def test_rejects_an_unknown_suite() -> None:
    with pytest.raises(GuideError, match="unknown suite"):
        check_test_selection(_sel(suites=("unit",)))


def test_rejects_both_artifact_and_url() -> None:
    with pytest.raises(GuideError, match="exactly one source"):
        check_test_selection(_sel(sdk_url="https://example.invalid/sdk.tar.gz"))


def test_rejects_no_sdk_source() -> None:
    with pytest.raises(GuideError, match="no SDK source"):
        check_test_selection(_sel(sdk_artifact=""))


def test_rejects_changed_in_with_explicit_cases() -> None:
    with pytest.raises(GuideError, match="mutually exclusive"):
        check_test_selection(_sel(changed_in="HEAD"))


def test_rejects_auto_size_for_conformance() -> None:
    """The workflow's sizing step cannot count a conformance run."""
    with pytest.raises(GuideError, match="cannot size a conformance run"):
        check_test_selection(_sel(suites=("conformance",), cases=(), paths=("api/",)))


def test_rejects_an_empty_selection() -> None:
    """Both harnesses exit 0 on an empty selection, so it must not reach them."""
    with pytest.raises(GuideError, match="no cases selected"):
        check_test_selection(_sel(cases=()))


def test_accepts_a_well_formed_selection() -> None:
    check_test_selection(_sel())


def _art(name: str, run_id: str = "1", expired: bool = False) -> Artifact:
    return Artifact(
        name=name,
        run_id=run_id,
        size_in_bytes=1,
        expired=expired,
        expires_at="2027-01-01T00:00:00Z",
        created_at="2026-01-01T00:00:00Z",
    )


def test_sdk_artifact_selection_filters_by_target() -> None:
    """A matrix build uploads both targets; the linux VM can only use one."""
    arts = [_art("cangjie-windows-x64-0.0.0-dev"), _art("cangjie-linux-x64-0.0.0-dev", "2")]
    found = find_sdk_artifact(arts, "linux-x64")
    assert found is not None and found.run_id == "2"


def test_sdk_artifact_selection_ignores_test_reports() -> None:
    arts = [_art("cangjie-test-reports-9"), _art("cangjie-linux-x64-0.0.0-dev", "3")]
    found = find_sdk_artifact(arts, "linux-x64")
    assert found is not None and found.run_id == "3"


def test_sdk_archive_name_differs_from_the_artifact_name() -> None:
    """The artifact wraps cangjie-sdk-<target>-<ver>.tar.gz, and is named cangjie-<target>-<ver>."""
    assert sdk_archive_name("linux-x64", "0.0.0-dev") == "cangjie-sdk-linux-x64-0.0.0-dev"
