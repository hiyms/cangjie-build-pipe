from __future__ import annotations

from collections.abc import Mapping

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
            inputs[f"{name}_source"] = value
    format_sources(inputs)
    return inputs


def format_source(value: str) -> str:
    """Turn URL#REF into the URL:REF form the workflows read.

    `#` separates instead of `:` because a URL already carries a colon, and the
    workflow's own parser splits on the last colon. A value with no `#` is
    returned unchanged, so an already-converted source survives a second pass.
    """
    url, _, ref_part = value.partition("#")
    return f"{url}:{ref_part}" if ref_part else url


def format_sources(inputs: dict[str, str]) -> None:
    """Format every source override in place, however it was entered.

    Both the command line and the guided form take URL#REF, so the conversion
    happens after either has supplied values; running it twice is harmless
    because a value without `#` is left alone.
    """
    for key, value in inputs.items():
        if key.endswith("_source") and value:
            inputs[key] = format_source(value)
