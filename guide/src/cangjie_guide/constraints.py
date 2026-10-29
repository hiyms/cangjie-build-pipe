from __future__ import annotations

from dataclasses import dataclass

from cangjie_guide.errors import GuideError

# Rules the workflows enforce in shell, which no YAML field can express. Kept as
# data so a test can assert them against the workflow text; a rule that lives
# only in a `raise` drifts the moment someone edits the shell.
SUITE_NAMES: tuple[str, ...] = ("conformance", "hlt", "llt")

# test-cangjie-azure.yml rejects a conformance run whose vm_size is "auto": the
# size step counts cases, and conformance has no case list to count.
AUTO_SIZE_REJECTS_CONFORMANCE = True


@dataclass(frozen=True)
class SuiteSelection:
    suites: tuple[str, ...]
    cases: tuple[str, ...]
    paths: tuple[str, ...]
    changed_in: str
    test_list: str
    sdk_artifact: str
    sdk_url: str
    vm_size: str


def check_test_selection(sel: SuiteSelection) -> None:
    """Reject combinations the workflow would reject later, before a VM exists."""
    unknown = [s for s in sel.suites if s not in SUITE_NAMES]
    if unknown:
        raise GuideError(f"unknown suite(s) {', '.join(unknown)}; valid: {', '.join(SUITE_NAMES)}")

    if sel.sdk_artifact and sel.sdk_url:
        raise GuideError("--sdk resolved to both an artifact and a URL; give exactly one source")
    if not sel.sdk_artifact and not sel.sdk_url:
        raise GuideError("no SDK source: pass --sdk <run-id|name|url|latest>")

    if sel.changed_in and sel.cases:
        raise GuideError(
            "changed_in and explicit cases are mutually exclusive: "
            "the workflow rejects a run that names both"
        )

    if AUTO_SIZE_REJECTS_CONFORMANCE and sel.vm_size == "auto" and "conformance" in sel.suites:
        raise GuideError("vm_size=auto cannot size a conformance run; pass an explicit --vm-size")

    # An empty selection makes both harnesses exit 0 without running anything.
    selectors = [bool(sel.cases), bool(sel.paths), bool(sel.test_list), bool(sel.changed_in)]
    if "conformance" not in sel.suites and not any(selectors):
        raise GuideError(
            "no cases selected: pass --case, --tests, --test-list or --changed-in; "
            "an empty selection reports success without running anything"
        )
