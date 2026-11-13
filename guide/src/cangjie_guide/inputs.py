from __future__ import annotations

import re
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

    Three shapes are accepted: a clone URL on its own, ``URL#REF``, and ``#REF``
    for the default repository of that name with its own ref. The ref follows the
    URL after `#`, because a URL already carries colons (the scheme, the port)
    and a branch name carries slashes (feat/windows-cross). A value with a colon
    in the path is the shape that used to be accepted, and it reaches git as part
    of the URL: the clone then asks the forge for a repository named after the
    branch. Catching it here names the typo instead.

    Every rejection here mirrors `.github/scripts/build-fetch-args.sh`, which
    refuses the same shapes inside the workflow. A value this check accepted but
    the script refused would be dispatched, provision a VM, and fail there.
    """
    url, separator, ref_part = value.partition("#")
    url = url.strip()
    ref = ref_part.strip()
    if separator and not ref:
        raise GuideError(f"source {value!r} ends with '#' and no ref")
    if separator and any(ch.isspace() for ch in ref_part):
        raise GuideError(f"source {value!r} has whitespace inside the ref")
    if ref.startswith("-"):
        raise GuideError(
            f"source {value!r} has a ref beginning with '-', which git reads as an option"
        )
    if not url:
        # `#REF` keeps the repository default; only the ref is being overridden.
        return f"#{ref}"
    path = url.split("://", 1)[-1]
    if "/" in path and ":" in path[path.index("/") :]:
        raise GuideError(f"source {value!r} separates the ref with ':' in the path; write URL#ref")
    # scp-style `git@host:owner/repo.git` carries its path after a colon. The
    # workflow prefixes a scheme-less value with https://, which would put that
    # colon in the host, so it refuses the shape; a port number is the one
    # colon-shaped input that is legitimate.
    if "://" not in url:
        first_segment = url.split("/", 1)[0]
        if ":" in first_segment and not re.search(r":\d+$", first_segment):
            raise GuideError(
                f"source {value!r} looks like scp syntax; write it as "
                "ssh://git@host/path/to/repo.git"
            )
    return value


def check_sources(inputs: dict[str, str]) -> None:
    """Validate every source override in place.

    The flags and the guided form both produce `*_source` values, so the check
    runs after either has supplied them.
    """
    for key, value in inputs.items():
        if key.endswith("_source") and value:
            inputs[key] = check_source(value)
