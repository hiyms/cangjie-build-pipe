from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from cangjie_guide import constraints, doctor, registry, runs
from cangjie_guide.errors import GuideError
from cangjie_guide.gh import Gh, GhError
from cangjie_guide.schema import Artifact, Workflow

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Drive the Cangjie SDK build and test workflows from this machine.",
)
console = Console()

# Where downloaded artifacts land. Outside the repository on purpose: a build
# archive is ~800 MB, and the default of the current directory would drop it
# into the worktree.
CACHE_DIR = Path.home() / ".cache" / "cangjie-guide"

DEFAULT_REF = "master"


class Context:
    def __init__(self, *, repo: str | None, ref: str, tui: bool | None) -> None:
        self.gh = Gh()
        self.ref = ref
        self._repo = repo
        self._tui = tui

    @property
    def explicit_repo(self) -> str | None:
        """The --repo value, or None when it should be inferred."""
        return self._repo

    @property
    def repo(self) -> str:
        if self._repo is None:
            self._repo = self.gh.current_repo()
        return self._repo

    def workflow(self, path: str) -> Workflow:
        return registry.load(self.gh, self.repo, path, self.ref)

    def use_tui(self, *, required: bool) -> bool:
        """Whether to fall back to guided input.

        `--tui` forces it, `--no-tui` forbids it, and with neither the answer
        depends on whether the command has everything it needs and whether a
        human is at the terminal.
        """
        if self._tui is True:
            return True
        if self._tui is False:
            if required:
                raise GuideError("missing required arguments and --no-tui was given")
            return False
        return required and sys.stdout.isatty()


@app.callback()
def main(
    ctx: typer.Context,
    repo: Annotated[
        str | None, typer.Option("--repo", help="owner/name; defaults to this checkout's remote.")
    ] = None,
    ref: Annotated[
        str, typer.Option("--ref", help="Git ref the workflow runs from.")
    ] = DEFAULT_REF,
    tui: Annotated[
        bool | None, typer.Option("--tui/--no-tui", help="Force or forbid guided input.")
    ] = None,
) -> None:
    ctx.obj = Context(repo=repo, ref=ref, tui=tui)


def _obj(ctx: typer.Context) -> Context:
    obj: Context = ctx.obj
    return obj


def _render_table(title: str, headers: list[str], rows: list[list[str]]) -> None:
    table = Table(title=title, show_header=True, header_style="bold")
    for head in headers:
        table.add_column(head)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def _status_mark(status: str) -> str:
    return {"ok": "[green]ok[/green]", "fail": "[red]fail[/red]"}.get(status, "[yellow]?[/yellow]")


# -- build --------------------------------------------------------------------


@app.command()
def build(
    ctx: typer.Context,
    target: Annotated[str, typer.Option(help="SDK target.")] = "linux-x64",
    tag: Annotated[str, typer.Option(help="Upstream ref to build.")] = "main",
    build_type: Annotated[str, typer.Option("--build-type", help="Build type.")] = "relwithdebinfo",
    compiler: Annotated[
        str | None, typer.Option(help="Override compiler source as URL#REF.")
    ] = None,
    runtime: Annotated[str | None, typer.Option(help="Override runtime source as URL#REF.")] = None,
    tools: Annotated[str | None, typer.Option(help="Override tools source as URL#REF.")] = None,
    stdx: Annotated[str | None, typer.Option(help="Override stdx source as URL#REF.")] = None,
    vm_size: Annotated[str, typer.Option("--vm-size", help="Azure SKU.")] = "Standard_F16als_v7",
    region: Annotated[
        str, typer.Option(help="Space separated region preference.")
    ] = "eastus eastus2 centralus westus3",
    priority: Annotated[str, typer.Option(help="Spot or Regular.")] = "Spot",
    disk_size_gb: Annotated[str, typer.Option("--disk-size-gb", help="OS disk size.")] = "128",
    disk_sku: Annotated[str, typer.Option("--disk-sku", help="OS disk SKU.")] = "StandardSSD_LRS",
    cache_container: Annotated[
        str, typer.Option("--cache-container", help="Blob container for the toolchain cache.")
    ] = "toolchain",
    hosted: Annotated[
        bool, typer.Option(help="Use the GitHub-hosted workflow instead of Azure.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Proceed even if an artifact of the same version exists.")
    ] = False,
    wait: Annotated[bool, typer.Option("--wait", help="Follow the run to completion.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be dispatched and stop.")
    ] = False,
) -> None:
    """Build the SDK."""
    obj = _obj(ctx)
    path = registry.BUILD_HOSTED if hosted else registry.BUILD_AZURE
    workflow = obj.workflow(path)

    inputs: dict[str, str] = {"tag": tag, "build_type": build_type}
    if not hosted:
        inputs |= {
            "target": target,
            "vm_size": vm_size,
            "vm_regions": region,
            "vm_priority": priority,
            "os_disk_size_gb": disk_size_gb,
            "os_disk_sku": disk_sku,
            "cache_container": cache_container,
        }
    for name, value in (
        ("compiler", compiler),
        ("runtime", runtime),
        ("tools", tools),
        ("stdx", stdx),
    ):
        if value:
            url, _, ref_part = value.partition("#")
            inputs[f"{name}_source"] = f"{url}:{ref_part}" if ref_part else url

    _check_inputs(workflow, inputs)

    if not hosted:
        _guard_conflicts(obj, workflow, inputs, force=force)
    _guard_active_runs(obj, workflow)

    if dry_run:
        _print_dispatch(workflow, inputs, obj.ref)
        return
    _dispatch(obj, workflow, inputs, wait=wait)


def _guard_conflicts(
    obj: Context, workflow: Workflow, inputs: dict[str, str], *, force: bool
) -> None:
    """Refuse to overwrite an artifact another run may be using.

    upload-artifact replaces same-named artifacts, and the version is normalised
    (tag=main and tag=dev both become 0.0.0-dev), so rebuilding silently
    replaces whatever the previous run produced.
    """
    version_input = workflow.input("tag")
    raw = inputs.get("tag", version_input.default if version_input else "")
    # Local normalisation mirrors the workflow's print-version step; a value that
    # is not SemVer becomes the placeholder the harness uses.
    from cangjie_guide.version import normalise

    version = normalise(raw)
    target = inputs.get("target", "linux-x64")
    name = f"cangjie-{target}-{version}"
    existing = runs.conflicting_artifacts(obj.gh, obj.repo, name)
    if not existing or force:
        return

    table_rows = [
        [art.run_id, art.created_at, art.expires_at, str(art.size_in_bytes)] for art in existing
    ]
    _render_table(
        f"An artifact named {name} already exists and would be replaced",
        ["run", "created", "expires", "bytes"],
        table_rows,
    )
    if obj.use_tui(required=True):
        if _confirm_overwrite(name):
            return
        raise GuideError("aborted: would have overwritten " + name)
    raise GuideError(
        f"{name} already exists and a new build would replace it; re-run with --force to accept"
    )


def _confirm_overwrite(name: str) -> bool:
    from cangjie_guide.tui import confirm

    return confirm(f"Replace the existing artifact {name}?")


def _guard_active_runs(obj: Context, workflow: Workflow) -> None:
    active = runs.active_runs(obj.gh, workflow.file)
    if not active:
        return
    listing = ", ".join(f"{r['databaseId']}({r['status']})" for r in active)
    raise GuideError(
        f"{workflow.display_name} already has queued or running runs: {listing}. "
        "This repository declares no concurrency, so dispatching again provisions "
        "a second VM instead of queueing."
    )


def _check_inputs(workflow: Workflow, inputs: dict[str, str]) -> None:
    for key, value in inputs.items():
        spec = workflow.input(key)
        if spec is None:
            raise GuideError(f"{workflow.file} has no input named {key!r}")
        if spec.is_choice and value not in spec.options:
            raise GuideError(f"{key}={value!r} is not one of {', '.join(spec.options)}")


def _print_dispatch(workflow: Workflow, inputs: dict[str, str], ref: str) -> None:
    rows = [[k, v] for k, v in sorted(inputs.items())]
    _render_table(f"Would dispatch {workflow.display_name} at {ref}", ["input", "value"], rows)


def _dispatch(obj: Context, workflow: Workflow, inputs: dict[str, str], *, wait: bool) -> None:
    run_id = runs.dispatch(obj.gh, workflow, inputs, ref=obj.ref)
    console.print(
        f"dispatched {workflow.display_name}: https://github.com/{obj.repo}/actions/runs/{run_id}"
    )
    if wait:
        _watch(obj, run_id)


def _watch(obj: Context, run_id: str) -> None:
    payload = runs.wait(obj.gh, run_id, on_poll=lambda p: console.print(f"  {p.get('status')}"))
    conclusion = payload.get("conclusion")
    console.print(f"run {run_id} finished: {conclusion}")
    if conclusion != "success":
        raise typer.Exit(code=1)


# -- test ---------------------------------------------------------------------


@app.command()
def test(
    ctx: typer.Context,
    sdk: Annotated[
        str | None, typer.Option(help="Run id, artifact name, URL, or 'latest'.")
    ] = None,
    suites: Annotated[
        list[str] | None, typer.Option("--suites", help="One or more of conformance, hlt, llt.")
    ] = None,
    case: Annotated[list[str] | None, typer.Option("--case", help="Case path; repeatable.")] = None,
    tests: Annotated[
        list[str] | None, typer.Option("--tests", help="Conformance path mask; repeatable.")
    ] = None,
    changed_in: Annotated[
        str, typer.Option("--changed-in", help="Select cases touched by one commit.")
    ] = "",
    test_list: Annotated[str, typer.Option("--test-list", help="Framework test list file.")] = "",
    test_repo: Annotated[
        str, typer.Option("--test-repo", help="cangjie_test repository URL.")
    ] = "",
    test_tag: Annotated[str, typer.Option("--test-tag", help="Ref for cangjie_test.")] = "main",
    framework_tag: Annotated[
        str, typer.Option("--framework-tag", help="Ref for cangjie_test_framework.")
    ] = "",
    jobs: Annotated[str, typer.Option(help="Parallel cases; empty derives from VM size.")] = "",
    base_timeout: Annotated[
        str, typer.Option("--base-timeout", help="Per-case timeout in seconds.")
    ] = "120",
    vm_size: Annotated[str, typer.Option("--vm-size", help="SKU or 'auto'.")] = "auto",
    region: Annotated[
        str, typer.Option(help="Space separated region preference.")
    ] = "eastus eastus2 centralus westus3",
    priority: Annotated[str, typer.Option(help="Spot or Regular.")] = "Spot",
    wait: Annotated[bool, typer.Option("--wait", help="Follow the run to completion.")] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be dispatched and stop.")
    ] = False,
) -> None:
    """Run cangjie_test suites against a built SDK."""
    obj = _obj(ctx)
    workflow = obj.workflow(registry.TEST_AZURE)

    if sdk is None:
        if not obj.use_tui(required=True):
            raise GuideError(
                "--sdk is required: pass a run id, an artifact name, a URL, or 'latest'"
            )
        from cangjie_guide.tui import pick_sdk

        sdk = pick_sdk(_artifact_choices(obj))

    suite_list = tuple(suites) if suites else ()
    if not suite_list:
        if not obj.use_tui(required=True):
            raise GuideError("--suites is required: one or more of conformance, hlt, llt")
        from cangjie_guide.tui import pick_suites

        suite_list = pick_suites()

    artifact_name, artifact_run, url = _resolve_sdk(obj, sdk)
    sel = constraints.SuiteSelection(
        suites=suite_list,
        cases=tuple(case or ()),
        paths=tuple(tests or ()),
        changed_in=changed_in,
        test_list=test_list,
        sdk_artifact=artifact_name,
        sdk_url=url,
        vm_size=vm_size,
    )
    constraints.check_test_selection(sel)

    inputs: dict[str, str] = {
        "suites": " ".join(suite_list),
        "base_timeout": base_timeout,
        "vm_size": vm_size,
        "vm_regions": region,
        "vm_priority": priority,
        "test_tag": test_tag,
    }
    if artifact_name:
        inputs["sdk_artifact"] = artifact_name
        inputs["sdk_artifact_run_id"] = artifact_run
    if url:
        inputs["sdk_url"] = url
    if case:
        inputs["test_cases"] = " ".join(case)
    if tests:
        inputs["test_paths"] = " ".join(tests)
    for key, value in (
        ("changed_in", changed_in),
        ("test_list", test_list),
        ("test_repo", test_repo),
        ("framework_tag", framework_tag),
        ("jobs", jobs),
    ):
        if value:
            inputs[key] = value

    _check_inputs(workflow, inputs)
    _guard_active_runs(obj, workflow)

    if dry_run:
        _print_dispatch(workflow, inputs, obj.ref)
        return
    _dispatch(obj, workflow, inputs, wait=wait)


def _artifact_choices(obj: Context) -> list[Artifact]:
    """Every unexpired linux SDK artifact, newest first."""
    arts = [Artifact.from_api(raw) for raw in obj.gh.repo_artifacts(obj.repo)]
    usable = [a for a in arts if not a.expired and a.name.startswith("cangjie-linux-x64-")]
    usable.sort(key=lambda a: a.created_at, reverse=True)
    return usable


def _resolve_sdk(obj: Context, sdk: str) -> tuple[str, str, str]:
    """Turn --sdk into (artifact name, source run id, direct url).

    Only one of the first two and the third is set. A direct URL bypasses the
    artifact entirely.
    """
    if sdk.startswith("http://") or sdk.startswith("https://"):
        return "", "", sdk

    if sdk == "latest" or sdk.isdigit():
        if sdk == "latest":
            choices = _artifact_choices(obj)
            if not choices:
                raise GuideError("no unexpired linux SDK artifact found")
            art = choices[0]
        else:
            found = runs.find_sdk_artifact(runs.artifacts_of(obj.gh, sdk), "linux-x64")
            if found is None:
                raise GuideError(f"run {sdk} has no unexpired linux-x64 SDK artifact")
            art = found
        if art.expired:
            raise GuideError(f"artifact {art.name} expired at {art.expires_at}")
        return art.name, art.run_id or sdk, ""

    # A literal artifact name: find which run produced it so the workflow can
    # fetch it, and confirm it is still there.
    for art in _artifact_choices(obj):
        if art.name == sdk:
            return art.name, art.run_id, ""
    raise GuideError(
        f"no unexpired artifact named {sdk}; listing usable ones is "
        "`cangjie-guide artifacts --search`"
    )


# -- status / logs / artifacts / cancel --------------------------------------


@app.command("runs")
def runs_command(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(help="How many runs to list.")] = 10,
) -> None:
    """List recent runs."""
    obj = _obj(ctx)
    payload: list[dict[str, Any]] = obj.gh.json(
        [
            "run",
            "list",
            "--limit",
            str(limit),
            "--json",
            "databaseId,workflowName,status,conclusion,headBranch,createdAt",
        ]
    )
    rows = [
        [
            str(r.get("databaseId", "")),
            str(r.get("workflowName", "")),
            str(r.get("headBranch", "")),
            str(r.get("status", "")),
            str(r.get("conclusion", "")),
            str(r.get("createdAt", "")),
        ]
        for r in payload
    ]
    _render_table(
        "Recent runs", ["run", "workflow", "ref", "status", "conclusion", "created"], rows
    )


@app.command()
def status(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Argument(help="Run id; omit for recent runs.")] = None,
    watch: Annotated[bool, typer.Option("--watch", help="Follow until completion.")] = False,
) -> None:
    """Show run status."""
    obj = _obj(ctx)
    if run_id is None:
        runs_command(ctx, limit=10)
        return
    if watch:
        _watch(obj, run_id)
        return
    payload = obj.gh.run_view(run_id, "status,conclusion,jobs,displayTitle,url")
    console.print(
        f"{payload.get('displayTitle')} {payload.get('status')} {payload.get('conclusion')}"
    )
    rows: list[list[str]] = []
    jobs: list[dict[str, Any]] = payload.get("jobs", [])
    for job in jobs:
        steps: list[dict[str, Any]] = job.get("steps", [])
        current: dict[str, Any] | None = next(
            (s for s in steps if s.get("status") == "in_progress"), None
        )
        rows.append(
            [
                str(job.get("name", "")),
                str(job.get("status", "")),
                str(job.get("conclusion", "")),
                str(current.get("name", "")) if current else "",
            ]
        )
    _render_table(f"run {run_id}", ["job", "status", "conclusion", "current step"], rows)


@app.command()
def logs(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Argument()] = None,
    job: Annotated[str | None, typer.Option(help="Job database id.")] = None,
) -> None:
    """Print run logs."""
    obj = _obj(ctx)
    if run_id is None:
        if not obj.use_tui(required=True):
            raise GuideError("run id is required")
        from cangjie_guide.tui import pick_run

        run_id = pick_run(_recent_runs(obj))
    args = ["run", "view", run_id, "--log"]
    if job:
        args += ["--job", job]
    console.print(obj.gh.text(args))


@app.command()
def artifacts(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Argument()] = None,
    get: Annotated[str | None, typer.Option("--get", help="Artifact name to download.")] = None,
    search: Annotated[
        bool, typer.Option("--search", help="List every unexpired SDK artifact.")
    ] = False,
) -> None:
    """List or download artifacts."""
    obj = _obj(ctx)
    if search:
        rows = [
            [a.name, a.run_id, a.created_at, a.expires_at, str(a.size_in_bytes)]
            for a in _artifact_choices(obj)
        ]
        _render_table(
            "Unexpired linux SDK artifacts", ["name", "run", "created", "expires", "bytes"], rows
        )
        return

    if run_id is None:
        if not obj.use_tui(required=True):
            raise GuideError("run id is required")
        from cangjie_guide.tui import pick_run

        run_id = pick_run(_recent_runs(obj))

    available = runs.artifacts_of(obj.gh, run_id)
    rows = [
        [a.name, "expired" if a.expired else "ok", a.expires_at, str(a.size_in_bytes)]
        for a in available
    ]
    _render_table(f"artifacts of run {run_id}", ["name", "state", "expires", "bytes"], rows)

    if get:
        dest = CACHE_DIR / run_id
        dest.mkdir(parents=True, exist_ok=True)
        runs.download(obj.gh, run_id, get, str(dest))
        console.print(f"downloaded to {dest}")


@app.command()
def cancel(
    ctx: typer.Context,
    run_id: Annotated[str | None, typer.Argument()] = None,
) -> None:
    """Cancel a run.

    Cancelling does not reclaim the VM by itself: teardown rides on the cleanup
    job, which can fail, and the hourly reaper then collects leaked groups up to
    eight hours later.
    """
    obj = _obj(ctx)
    if run_id is None:
        if not obj.use_tui(required=True):
            raise GuideError("run id is required")
        from cangjie_guide.tui import pick_run

        run_id = pick_run(_recent_runs(obj))
    runs.cancel(obj.gh, run_id)
    console.print(f"cancel requested for {run_id}")
    console.print(
        "the VM is removed by the cleanup job, which can fail; leaked groups are "
        "collected by the hourly reaper up to 8 hours later"
    )


def _recent_runs(obj: Context) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = obj.gh.json(
        [
            "run",
            "list",
            "--limit",
            "20",
            "--json",
            "databaseId,workflowName,status,conclusion,createdAt",
        ]
    )
    return payload


# -- verbose ------------------------------------------------------------------


@app.command()
def verbose(
    ctx: typer.Context,
    ref: Annotated[
        str | None, typer.Option("--check-ref", help="Ref to verify workflow files at.")
    ] = None,
) -> None:
    """Check local gh readiness and the target repository's configuration."""
    obj = _obj(ctx)
    try:
        checks = doctor.run(obj.gh, repo=obj.explicit_repo, ref=ref or obj.ref)
    except GhError as exc:
        console.print(f"[red]failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    current_group = ""
    for check in checks:
        group = check.name.split(" ", 1)[0]
        if group != current_group:
            current_group = group
        console.print(f"  {_status_mark(str(check.status)):20} {check.name:32} {check.detail}")

    failed = [c for c in checks if c.status == doctor.Status.FAIL]
    if failed:
        console.print(f"\n[red]{len(failed)} check(s) failed[/red]")
        raise typer.Exit(code=1)
    console.print("\n[green]all checks passed[/green]")


def entrypoint() -> None:
    try:
        app()
    except GuideError as exc:
        Console(stderr=True).print(f"[red]error:[/red] {exc}")
        raise SystemExit(1) from exc
    except GhError as exc:
        Console(stderr=True).print(f"[red]gh failed:[/red] {exc}")
        raise SystemExit(1) from exc
