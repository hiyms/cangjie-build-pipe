from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cangjie_guide.errors import GuideError
from cangjie_guide.inputs import build_inputs, check_source
from cangjie_guide.registry import dispatch_inputs, normalise_keys
from cangjie_guide.schema import Workflow

ROOT = Path(__file__).resolve().parents[2]
AZURE = ".github/workflows/build-cangjie-azure.yml"
HOSTED = ".github/workflows/build-cangjie.yml"


def _workflow(path: str) -> Workflow:
    document = normalise_keys(yaml.safe_load((ROOT / path).read_text(encoding="utf-8")))
    return Workflow(
        file=path, display_name=path, ref="main", inputs=dispatch_inputs(document, path)
    )


def _inputs(
    path: str, *, version: str | None = None, tag: str = "main", compiler: str | None = None
) -> dict[str, str]:
    return build_inputs(
        _workflow(path),
        version=version,
        tag=tag,
        target="linux-x64",
        build_type="relwithdebinfo",
        sources={"compiler": compiler},
        vm_size="Standard_F16als_v7",
        regions="eastus",
        priority="Spot",
        disk_size_gb="128",
        disk_sku="StandardSSD_LRS",
        cache_container="toolchain",
        hosted=path == HOSTED,
    )


def test_both_build_workflows_take_a_version() -> None:
    """One input, so guide and the run form always mean the same thing."""
    for path in (AZURE, HOSTED):
        spec = {i.name: i for i in _workflow(path).inputs}["version"]
        assert spec.default == "", path
        assert not spec.required, path


def test_the_version_flag_reaches_the_dispatch() -> None:
    inputs = _inputs(AZURE, version="1.0.2")
    assert inputs["version"] == "1.0.2"
    # The tag still selects the sources; the version only names the result.
    assert inputs["tag"] == "main"


def test_an_unset_version_leaves_the_tag_in_charge() -> None:
    """Empty is what the workflow reads as "derive the version from the tag"."""
    inputs = _inputs(AZURE, tag="release/1.0")
    assert inputs["version"] == ""
    assert inputs["tag"] == "release/1.0"


def test_a_source_override_travels_as_entered() -> None:
    """The workflow splits on `#`, so the value must not be rewritten here."""
    inputs = _inputs(AZURE, compiler="https://example.invalid/cc.git#feature/x")
    assert inputs["compiler_source"] == "https://example.invalid/cc.git#feature/x"


def test_a_source_override_may_not_use_the_colon_form() -> None:
    """That shape reaches git as part of the URL and fails at the clone."""
    with pytest.raises(GuideError, match=r"URL#ref"):
        check_source("https://example.invalid/cc.git:feature/x")


def test_a_source_override_needs_a_url_and_a_ref() -> None:
    with pytest.raises(GuideError, match="no clone URL"):
        check_source("#feature/x")
    with pytest.raises(GuideError, match="no ref"):
        check_source("https://example.invalid/cc.git#")


def test_the_hosted_workflow_gets_no_vm_inputs() -> None:
    """Only the Azure workflow provisions a VM; the other ignores such a flag."""
    hosted = _inputs(HOSTED)
    assert "vm_size" not in hosted
    assert "target" not in hosted
    assert hosted["build_type"] == "relwithdebinfo"


def test_every_input_the_workflow_declares_is_filled_in() -> None:
    """An unknown key is rejected at dispatch, so no declared input may be missing."""
    for path in (AZURE, HOSTED):
        inputs = _inputs(path)
        for spec in _workflow(path).inputs:
            assert spec.name in inputs, spec.name
