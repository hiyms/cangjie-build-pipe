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
            ".github/workflows/test-cangjie-azure.yml",
            ".github/workflows/reap-orphans.yml",
        )
    )

    assert "azure-provision-vm.sh" in scripts
    assert "azure-teardown.sh" in scripts
    assert "azure-reap-orphans.sh" in scripts

    assert "provision-vm.sh" not in scripts
    assert "teardown.sh" not in scripts
    assert "reap-orphans.sh" not in scripts
    assert ".github/scripts/azure-provision-vm.sh" in workflows
    assert ".github/scripts/azure-teardown.sh" in workflows
    assert ".github/scripts/azure-reap-orphans.sh" in workflows
    assert ".github/scripts/provision-vm.sh" not in workflows
    assert ".github/scripts/teardown.sh" not in workflows
    assert ".github/scripts/reap-orphans.sh" not in workflows


def test_gcp_support_is_removed() -> None:
    """The project is Azure-only; a leftover GCP reference means it was not."""
    scripts = {path.name for path in (ROOT / ".github" / "scripts").glob("*.sh")}
    workflows = {path.name for path in (ROOT / ".github" / "workflows").glob("*.yml")}

    assert not any(name.startswith("gcp-") for name in scripts), scripts
    assert not any("gcp" in name for name in workflows), workflows
    for path in (
        ".github/workflows/reap-orphans.yml",
        ".github/workflows/build-cangjie-azure.yml",
        ".github/workflows/test-cangjie-azure.yml",
        "README.md",
    ):
        body = _read(path)
        assert "GCP_SERVICE_ACCOUNT" not in body, path
        assert "gcp-" not in body, path


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
    # Existence comes from the subcommand's own True/False answer; a broken probe
    # must not be parsed as an absence.
    assert "az_blob exists" in body
    assert "grep -qiE 'BlobNotFound" not in body
    for workflow in (".github/workflows/build-cangjie-azure.yml",):
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


def test_azure_build_honours_the_target_input() -> None:
    body = _read(".github/workflows/build-cangjie-azure.yml")

    # The input exists and offers both targets.
    assert "      target:\n" in body
    assert "          - linux-x64\n" in body
    assert "          - windows-x64\n" in body

    # MinGW and its blob cache are windows-only; linux builds static libs instead.
    assert "if: inputs.target == 'windows-x64'" in body
    assert "if: inputs.target == 'linux-x64'" in body
    assert "install-static-libs" in body

    # Every harness invocation must take the target from the input, never pin it,
    # or selecting linux-x64 would silently build the Windows cross target.
    for line in body.splitlines():
        if "--target" not in line:
            continue
        # The static-lib step is itself gated on linux-x64, so naming it there is
        # the condition restated; everything else must follow the input.
        if "install-static-libs" in line:
            assert "--target linux-x64" in line, line
            continue
        assert "--target ${{ inputs.target }}" in line, line


def test_runner_version_is_recent_enough_to_be_accepted() -> None:
    """GitHub rejects runners past a deprecation cutoff; 2.334.0 was refused."""
    for path in (
        ".github/workflows/build-cangjie-azure.yml",
        ".github/workflows/test-cangjie-azure.yml",
    ):
        body = _read(path)
        for line in body.splitlines():
            if "RUNNER_VERSION:" not in line:
                continue
            version = line.split('"')[1]
            major, minor, _ = (int(p) for p in version.split("."))
            assert (major, minor) >= (2, 338), f"{path}: runner {version} is deprecated"


def test_azure_cli_install_covers_the_runner_and_is_scoped_to_windows() -> None:
    """The azure/cli action needs Docker, which the ephemeral image does not have."""
    body = _read(".github/workflows/build-cangjie-azure.yml")

    # The action runs the CLI in a container, so it cannot be used on this VM.
    assert "azure/cli@" not in body
    assert "packages.microsoft.com/repos/azure-cli" in body
    # Codename comes from os-release; lsb_release is not installed by cloud-init.
    assert "/etc/os-release" in body
    assert "lsb_release" not in body
    # Only the windows target touches the blob cache, so linux must skip the CLI.
    assert "      - name: Install Azure CLI\n        if: inputs.target == 'windows-x64'" in body


def test_sccache_connection_string_is_normalised_before_use() -> None:
    """sccache appends /<container>/<path>; a trailing slash makes it InvalidUri."""
    script = ".github/scripts/normalize-connection-string.sh"
    assert (ROOT / script).is_file()

    body = _read(".github/workflows/build-cangjie-azure.yml")
    assert "normalize-connection-string.sh" in body
    # The fix has to reach later steps, so it goes through GITHUB_ENV, and it
    # must run before any build step reads the variable.
    assert 'SCCACHE_AZURE_CONNECTION_STRING=$normalized" >> "$GITHUB_ENV"' in body
    assert body.index("Normalise the sccache connection string") < body.index("Build compiler")


def test_test_workflow_can_point_at_another_test_repository() -> None:
    """A pull request's cases live on a forge GitHub does not mirror."""
    body = _read(".github/workflows/test-cangjie-azure.yml")
    assert "      test_repo:\n" in body
    # Both the sizing step and the run step must use it, or the VM would be
    # sized from one repository and tested against another.
    assert body.count("--test-repo") == 2
    assert body.count("TEST_REPO: ${{ inputs.test_repo }}") == 2


def test_test_workflow_installs_uv_before_it_is_used() -> None:
    """The sizing step shells out to uv, so setup-uv has to come first."""
    import re

    body = _read(".github/workflows/test-cangjie-azure.yml")
    provision = body.split("  provision:", 1)[1].split("\n  run:", 1)[0]
    setup = provision.index("astral-sh/setup-uv")
    sizing = provision.index("Resolve VM size from the case count")
    assert setup < sizing
    assert "uv sync --frozen" in provision
    # And the step really is the first place uv appears.
    first_uv_use = re.search(r"^\s+(uv |uv$)", provision, re.MULTILINE)
    assert first_uv_use is not None and first_uv_use.start() > setup


def test_test_workflow_keeps_the_two_repository_tags_separate() -> None:
    body = _read(".github/workflows/test-cangjie-azure.yml")
    assert "      framework_tag:\n" in body
    assert 'description: "Branch, tag or commit SHA for cangjie_test."' in body
    assert body.count("--framework-tag") == 1
    # plan-tests never clones the framework, so it must not take the flag.
    sizing = body.split("Resolve VM size from the case count", 1)[1]
    sizing = sizing.split("- name:", 1)[0]
    assert "--framework-tag" not in sizing


def test_workflows_reference_actions_by_tag_not_by_sha() -> None:
    """A wrong SHA fails at resolve time, before any step runs."""
    import re

    uses = re.compile(r"uses: ([^\s@]+)@([^\s#]+)")
    for path in sorted(ROOT.glob(".github/workflows/*.yml")):
        for match in uses.finditer(path.read_text()):
            ref = match.group(2)
            assert not re.fullmatch(r"[0-9a-f]{40}", ref), f"{path.name}: pinned SHA {ref}"
            assert ref.startswith("v"), f"{path.name}: {match.group(1)}@{ref} is not a tag"


def test_os_disk_defaults_to_the_cheap_tier_and_an_explicit_size() -> None:
    """az defaults to Premium_LRS, which costs about four times more per hour."""
    script = _read(".github/scripts/azure-provision-vm.sh")
    assert "OS_DISK_SKU=${OS_DISK_SKU:-StandardSSD_LRS}" in script
    assert "OS_DISK_SIZE_GB=${OS_DISK_SIZE_GB:-128}" in script
    # Passing neither would let az pick a 30 GB Premium disk.
    assert '--storage-sku "$OS_DISK_SKU"' in script
    assert '--os-disk-size-gb "$OS_DISK_SIZE_GB"' in script

    build = _read(".github/workflows/build-cangjie-azure.yml")
    assert 'default: "StandardSSD_LRS"' in build
    assert "OS_DISK_SKU: ${{ inputs.os_disk_sku }}" in build

    test = _read(".github/workflows/test-cangjie-azure.yml")
    assert "OS_DISK_SKU: StandardSSD_LRS" in test


def test_pre_commit_hook_runs_the_same_checks_as_the_lint_job() -> None:
    """A hook that checks less than CI reports success for what it never ran."""
    hook = ROOT / ".githooks/pre-commit"
    assert hook.is_file()
    assert hook.stat().st_mode & 0o111, "hook is not executable"

    body = hook.read_text()
    ci = _read(".github/workflows/build-cangjie.yml")
    for command in (
        "uv run ruff check .",
        "uv run ruff format --check .",
        "shellcheck .github/scripts/*.sh",
        "shfmt -d .github/scripts",
        "uv run ty check",
        "uv run pyright",
        "uv run pytest",
    ):
        assert command in ci, f"CI no longer runs: {command}"
        assert command in body, f"hook does not run: {command}"


def test_guide_is_a_separate_project_that_ci_does_not_silently_skip() -> None:
    """The root checks exclude guide, so its own checks have to run somewhere."""
    root = _read("pyproject.toml")
    assert "guide" in root, "the root ruff config no longer mentions guide"

    guide = _read("guide/pyproject.toml")
    for key in ("[tool.ruff]", "[tool.pyright]", "[tool.pytest.ini_options]"):
        assert key in guide, key

    hook = _read(".githooks/pre-commit")
    # A second project escapes the root include lists; the hook must cover it.
    assert "if [[ -f guide/pyproject.toml ]]" in hook
    for command in ("ruff check", "pyright", "ty check", "pytest"):
        assert command in hook


def test_no_action_still_targets_the_deprecated_node_20_runtime() -> None:
    """GitHub annotates every run that uses one; checkout@v4 was the last."""
    body = "\n".join(
        _read(p)
        for p in (
            ".github/workflows/build-cangjie.yml",
            ".github/workflows/build-cangjie-azure.yml",
            ".github/workflows/test-cangjie-azure.yml",
            ".github/workflows/reap-orphans.yml",
        )
    )
    assert "actions/checkout@v4" not in body
    assert "actions/checkout@v5" in body


def test_sccache_annotation_is_disabled_and_replaced_by_a_summary() -> None:
    """The action samples stats after --stop-server, so it always reports zeros."""
    azure = _read(".github/workflows/build-cangjie-azure.yml")
    assert "disable_annotations: 'true'" in azure
    assert "sccache-summary.sh" in azure
    # The stats have to be captured before the server goes away. Comments mention
    # --stop-server earlier, so the comparison is against the command itself.
    capture = azure.index(".github/scripts/sccache-summary.sh")
    stop = azure.index("--stop-server || true")
    assert capture < stop


def test_test_stage_writes_a_summary() -> None:
    from cangjie_build import summary

    reports = [summary.SuiteReport("llt", {"total": 3, "PASS": 3})]
    assert "| LLT | 3 | 3 | 0 |" in summary.render(reports)
