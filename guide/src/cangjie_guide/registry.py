from __future__ import annotations

from typing import Any, cast

import yaml

from cangjie_guide.errors import GuideError
from cangjie_guide.gh import Gh
from cangjie_guide.schema import Workflow, WorkflowInput

# Only these may be dispatched. The list is an allowlist, not a filter, because
# listing every workflow turns the destructive one into a one-keystroke action:
# reap-orphans.yml takes no inputs and deletes every cangjie-* resource group
# older than its cutoff, so a form with nothing to fill in would be a button with
# nothing in front of it.
DISPATCHABLE: tuple[str, ...] = (
    ".github/workflows/build-cangjie-azure.yml",
    ".github/workflows/test-cangjie-azure.yml",
    ".github/workflows/build-cangjie.yml",
)

# Shown in listings but never dispatchable.
KNOWN_DESTRUCTIVE: tuple[str, ...] = (".github/workflows/reap-orphans.yml",)

BUILD_AZURE = ".github/workflows/build-cangjie-azure.yml"
TEST_AZURE = ".github/workflows/test-cangjie-azure.yml"
BUILD_HOSTED = ".github/workflows/build-cangjie.yml"

# Secrets each workflow reads, mirroring what its steps reference. Kept here
# rather than parsed: `secrets.X` sits deep inside step scripts, and a scan of
# the YAML would also match the commented-out header listing them.
WORKFLOW_SECRETS: dict[str, tuple[str, ...]] = {
    BUILD_AZURE: (
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "RUNNER_REGISTRATION_PAT",
        "CANGJIE_CACHE_CONNECTION_STRING",
        "SCCACHE_AZURE_CONNECTION_STRING",
    ),
    TEST_AZURE: (
        "AZURE_CLIENT_ID",
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "RUNNER_REGISTRATION_PAT",
    ),
    BUILD_HOSTED: (),
    KNOWN_DESTRUCTIVE[0]: ("AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_SUBSCRIPTION_ID"),
}


def _as_mapping(value: object, what: str) -> dict[Any, Any]:
    """A loaded YAML mapping, with its key type left as-is.

    Keys are not necessarily strings: an unquoted `on` is the boolean True, which
    is why normalise_keys exists.
    """
    if not isinstance(value, dict):
        raise GuideError(f"{what} is not a mapping")
    return cast(dict[Any, Any], value)


def normalise_keys(doc: object) -> dict[str, Any]:
    """Return the document with YAML 1.1 boolean keys spelled back as words.

    Unquoted `on:` is parsed as the boolean True, so `doc["on"]` raises KeyError
    and the trigger block becomes unreachable. YAML 1.1 also turns `yes`, `no`
    and `off` into booleans, so every boolean key is rewritten rather than only
    the one known to collide today.
    """
    mapping = _as_mapping(doc, "workflow YAML")
    renamed: dict[str, Any] = {}
    for raw_key, value in mapping.items():
        key: object = raw_key
        if key is True:
            renamed["on"] = value
        elif key is False:
            renamed["off"] = value
        else:
            renamed[str(key)] = value
    return renamed


def dispatch_inputs(document: dict[str, Any], path: str) -> tuple[WorkflowInput, ...]:
    trigger: object = document.get("on")
    # _as_mapping raises for a non-mapping, so no isinstance narrowing is needed;
    # narrowing here would hand it a bare dict the type checker cannot read.
    dispatch: object = _as_mapping(trigger, f"{path}: on").get("workflow_dispatch")
    if dispatch is None:
        raise GuideError(f"{path} is not dispatchable: no workflow_dispatch trigger")
    # `workflow_dispatch: {}` reaches here as an empty mapping: a real and
    # dangerous shape, dispatchable with nothing to review.
    inputs: object = _as_mapping(dispatch, f"{path}: workflow_dispatch").get("inputs")
    if inputs is None:
        return ()

    parsed: list[WorkflowInput] = []
    for raw_name, raw_spec in _as_mapping(inputs, f"{path}: workflow_dispatch.inputs").items():
        spec = _as_mapping(raw_spec, f"{path}: input {raw_name!r}")
        options: tuple[str, ...] = ()
        raw_options = spec.get("options")
        if isinstance(raw_options, list):
            options = tuple(str(option) for option in cast(list[Any], raw_options))
        default = spec.get("default")
        parsed.append(
            WorkflowInput(
                name=str(raw_name),
                default="" if default is None else str(default),
                required=bool(spec.get("required", False)),
                options=options,
                description=str(spec.get("description", "")),
            )
        )
    return tuple(parsed)


def load(gh: Gh, repo: str, path: str, ref: str) -> Workflow:
    """Read one workflow as committed at ref and turn it into a schema.

    The file comes from the dispatch ref rather than the worktree: a local
    checkout can be on another branch or carry uncommitted edits, and the form
    would then describe a workflow other than the one that runs.
    """
    text = gh.workflow_yaml(repo, path, ref)
    doc = normalise_keys(yaml.safe_load(text))
    name = doc.get("name")
    return Workflow(
        file=path,
        display_name=path if name is None else str(name),
        ref=ref,
        inputs=dispatch_inputs(doc, path),
        secrets=WORKFLOW_SECRETS.get(path, ()),
    )


def all_dispatchable(gh: Gh, repo: str, ref: str) -> tuple[Workflow, ...]:
    return tuple(load(gh, repo, path, ref) for path in DISPATCHABLE)
