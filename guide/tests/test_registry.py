from __future__ import annotations

from pathlib import Path

import yaml

from cangjie_guide.registry import dispatch_inputs, normalise_keys

ROOT = Path(__file__).resolve().parents[2]


def _doc(path: str) -> dict[str, object]:
    return normalise_keys(yaml.safe_load((ROOT / path).read_text(encoding="utf-8")))


def test_unquoted_on_key_is_parsed_as_a_boolean() -> None:
    """`on:` is a YAML 1.1 boolean, so doc["on"] raises without normalisation."""
    raw = yaml.safe_load((ROOT / ".github/workflows/build-cangjie-azure.yml").read_text())
    assert "on" not in raw, "PyYAML changed behaviour; normalise_keys may be unnecessary"
    assert True in raw
    assert "on" in normalise_keys(raw)


def test_normalise_keys_rewrites_every_boolean_key() -> None:
    renamed = normalise_keys({True: 1, False: 2, "name": 3})
    assert renamed == {"on": 1, "off": 2, "name": 3}


def test_azure_build_inputs_are_read_from_the_workflow() -> None:
    inputs = dispatch_inputs(_doc(".github/workflows/build-cangjie-azure.yml"), "build")
    by_name = {i.name: i for i in inputs}

    assert by_name["build_type"].options == ("release", "debug", "relwithdebinfo")
    assert by_name["build_type"].default == "relwithdebinfo"
    assert by_name["target"].options == ("linux-x64", "windows-x64")
    assert by_name["vm_priority"].options == ("Spot", "Regular")
    # A required input that also carries a default: the default is what the form
    # starts from, not an error condition.
    assert by_name["vm_size"].required and by_name["vm_size"].default == "Standard_F16als_v7"


def test_workflow_dispatch_without_inputs_yields_none() -> None:
    """`workflow_dispatch: {}` is dispatchable with nothing to review."""
    assert dispatch_inputs(_doc(".github/workflows/reap-orphans.yml"), "reap") == ()


def test_test_workflow_declares_the_ref_pair_the_driver_uses() -> None:
    inputs = dispatch_inputs(_doc(".github/workflows/test-cangjie-azure.yml"), "test")
    names = {i.name for i in inputs}
    assert {"sdk_artifact", "sdk_artifact_run_id", "suites", "test_tag", "framework_tag"} <= names


def test_default_ref_is_main_everywhere() -> None:
    """Upstream default branches are all main; dev is only a stale override."""
    for path, key in (
        (".github/workflows/build-cangjie-azure.yml", "tag"),
        (".github/workflows/build-cangjie.yml", "tag"),
        (".github/workflows/test-cangjie-azure.yml", "test_tag"),
    ):
        inputs = {i.name: i for i in dispatch_inputs(_doc(path), path)}
        assert inputs[key].default == "main", path
