from __future__ import annotations

from collections.abc import Mapping

from cangjie_guide.errors import GuideError
from cangjie_guide.schema import Workflow


def build_inputs(
    workflow: Workflow,
    *,
    version: str | None,
    tag: str,
    target: str,
    build_type: str,
    sources: Mapping[str, str | None],
    vm_size: str,
    regions: str,
    priority: str,
    disk_size_gb: str,
    disk_sku: str,
    cache_container: str,
    hosted: bool,
) -> dict[str, str]:
    """The dispatch inputs, starting from the defaults the workflow declares.

    Only the Azure workflow provisions a VM, so only it takes the machine
    inputs; filling the map from the schema keeps the two apart without a second
    copy of their input names, and keeps an input the workflow added from being
    dropped by a stale literal.
    """
    inputs: dict[str, str] = {spec.name: spec.default for spec in workflow.inputs}
    if "version" in inputs:
        # Empty means "derive the version from the tag", which is what the
        # workflow itself does with an unset version.
        inputs["version"] = "" if version is None else version
    inputs["tag"] = tag
    inputs["build_type"] = build_type
    if not hosted:
        inputs |= {
            "target": target,
            "vm_size": vm_size,
            "vm_regions": regions,
            "vm_priority": priority,
            "os_disk_size_gb": disk_size_gb,
            "os_disk_sku": disk_sku,
            "cache_container": cache_container,
        }
    for name, value in sources.items():
        if value:
            inputs[f"{name}_source"] = check_source(value)
    return inputs


def check_source(value: str) -> str:
    """Reject a source override the workflow would misread.

    The ref follows the URL after `#`, because a URL already carries colons (the
    scheme, the port) and a branch name carries slashes (feat/windows-cross).
    A value with a colon in the path is the shape that used to be accepted, and
    it reaches git as part of the URL: the clone then asks the forge for a
    repository named after the branch. Catching it here names the typo instead.
    """
    url, separator, ref_part = value.partition("#")
    if not url:
        raise GuideError(f"source {value!r} has no clone URL before the '#'")
    if separator and not ref_part.strip():
        raise GuideError(f"source {value!r} ends with '#' and no ref")
    path = url.split("://", 1)[-1]
    if "/" in path and ":" in path[path.index("/") :]:
        raise GuideError(f"source {value!r} separates the ref with ':' in the path; write URL#ref")
    return value


def check_sources(inputs: dict[str, str]) -> None:
    """Validate every source override in place.

    The flags and the guided form both produce `*_source` values, so the check
    runs after either has supplied them.
    """
    for key, value in inputs.items():
        if key.endswith("_source") and value:
            inputs[key] = check_source(value)
