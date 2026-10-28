from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_platform_specific_scripts_are_prefixed() -> None:
    scripts = {path.name for path in (ROOT / ".github" / "scripts").glob("*.sh")}
    workflows = "\n".join(
        _read(path)
        for path in (
            ".github/workflows/build-cangjie-azure.yml",
            ".github/workflows/build-cangjie-gcp.yml",
            ".github/workflows/test-cangjie-azure.yml",
            ".github/workflows/reap-orphans.yml",
        )
    )

    assert "azure-provision-vm.sh" in scripts
    assert "azure-teardown.sh" in scripts
    assert "azure-reap-orphans.sh" in scripts
    assert "gcp-provision-vm.sh" in scripts
    assert "gcp-teardown.sh" in scripts
    assert "gcp-reap-orphans.sh" in scripts

    assert "provision-vm.sh" not in scripts
    assert "teardown.sh" not in scripts
    assert "reap-orphans.sh" not in scripts
    assert ".github/scripts/azure-provision-vm.sh" in workflows
    assert ".github/scripts/azure-teardown.sh" in workflows
    assert ".github/scripts/azure-reap-orphans.sh" in workflows
    assert ".github/scripts/provision-vm.sh" not in workflows
    assert ".github/scripts/teardown.sh" not in workflows
    assert ".github/scripts/reap-orphans.sh" not in workflows


def test_gcp_provision_does_not_write_gcloud_output_to_github_outputs() -> None:
    script = _read(".github/scripts/gcp-provision-vm.sh")

    assert "-o none" not in script
    assert "--quiet >/dev/null" in script


def test_azure_spot_precheck_reads_low_priority_quota() -> None:
    """Spot allocation is gated by lowPriorityCores alone, so precheck it."""
    script = _read(".github/scripts/azure-provision-vm.sh")

    assert "lowPriorityCores" in script
    assert "quota_value" in script
    assert "--priority Spot" in script
    # Eviction uses delete semantics, so the OS disk must not outlive the VM.
    assert "--eviction-policy Delete" in script
    assert "--os-disk-delete-option Delete" in script


def test_azure_regular_precheck_covers_cores_and_family() -> None:
    """Regular is gated by both the regional and the per-family bucket."""
    script = _read(".github/scripts/azure-provision-vm.sh")

    assert "regular_regions" in script
    assert "quota_value cores" in script
    assert "sku_family" in script
    assert "sku_family()" in script
    # The family quota name is derived from the SKU, not read from the SKU
    # catalogue: that call walks the whole catalogue and times out.
    assert 'az vm list-skus -l "$1"' not in script


def test_azure_workflows_run_eviction_check_before_teardown() -> None:
    for workflow in (
        ".github/workflows/build-cangjie-azure.yml",
        ".github/workflows/test-cangjie-azure.yml",
    ):
        body = _read(workflow)
        assert "azure-eviction-check.sh" in body
        # The activity-log read only works while the resource group still exists.
        assert body.index("azure-eviction-check.sh") < body.index("azure-teardown.sh")


def test_toolchain_blob_script_is_prefixed_and_wired() -> None:
    """The toolchain cache lives in blob storage, not actions/cache."""
    body = _read(".github/scripts/toolchain-blob.sh")
    assert "CANGJIE_CACHE_CONNECTION_STRING" in body
    # A broken probe must not look like an empty cache.
    assert "BlobNotFound" in body
    for workflow in (
        ".github/workflows/build-cangjie-azure.yml",
        ".github/workflows/build-cangjie-gcp.yml",
    ):
        text = _read(workflow)
        assert "toolchain-blob.sh restore" in text
        assert "toolchain-blob.sh save" in text
        assert "CANGJIE_CACHE_CONNECTION_STRING" in text
        # The toolchain must not still be on the cache API with its 10 GB cap.
        assert "actions/cache/restore" not in text
        assert "actions/cache/save" not in text


def test_azure_workflows_use_oidc_not_a_static_password() -> None:
    for workflow in (
        ".github/workflows/build-cangjie-azure.yml",
        ".github/workflows/test-cangjie-azure.yml",
    ):
        text = _read(workflow)
        assert "azure/login@" in text
        assert "client-id: ${{ secrets.AZURE_CLIENT_ID }}" in text
        assert "tenant-id: ${{ secrets.AZURE_TENANT_ID }}" in text
        assert "subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}" in text
        # A client secret in the workflow would defeat the federated credential.
        assert "AZURE_CLIENT_SECRET" not in text
