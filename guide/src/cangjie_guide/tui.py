from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import questionary

from cangjie_guide.constraints import SUITE_NAMES
from cangjie_guide.errors import GuideError
from cangjie_guide.schema import Artifact, WorkflowInput
from cangjie_guide.version import explicit as explicit_version


def _answer(question: questionary.Question, what: str) -> Any:
    """Ask one question, refusing an aborted prompt.

    questionary returns None both for Ctrl-C and for a closed stdin, so a value
    taken straight from ask() could reach a dispatch as the literal string
    "None".
    """
    answer = question.ask()
    if answer is None:
        raise GuideError(f"cancelled while asking for {what}")
    return answer


def confirm(question: str, *, default: bool = True) -> bool:
    """A yes/no question; Enter accepts `default`."""
    return bool(_answer(questionary.confirm(question, default=default), question))


def choose(title: str, options: Sequence[tuple[str, Any]]) -> Any:
    """Pick one of the (label, value) pairs."""
    if not options:
        raise GuideError(f"{title}: nothing to choose from")
    choices = [questionary.Choice(title=label, value=value) for label, value in options]
    return _answer(questionary.select(title, choices=choices), title)


def _validator(spec: WorkflowInput) -> Callable[[str], bool | str]:
    """Per-input rule, evaluated as the user types."""

    def validate(value: str) -> bool | str:
        if spec.required and not value.strip():
            return f"{spec.name} is required"
        if spec.name == "version" and value.strip():
            try:
                explicit_version(value)
            except GuideError as exc:
                return str(exc)
        return True

    return validate


def ask_input(spec: WorkflowInput, current: str) -> str:
    """Ask for one workflow input, seeded with the value already decided.

    A choice input can only take one of the declared options, because the
    workflow rejects anything else. Everything else is free text, and Enter
    accepts the default the workflow declares.
    """
    prompt = f"{spec.name} ({spec.description})" if spec.description else spec.name
    if spec.is_choice:
        default = current if current in spec.options else spec.options[0]
        return str(
            _answer(
                questionary.select(prompt, choices=list(spec.options), default=default), spec.name
            )
        )
    return str(
        _answer(questionary.text(prompt, default=current, validate=_validator(spec)), spec.name)
    )


def pick_sdk(choices: Sequence[Artifact]) -> str:
    if not choices:
        raise GuideError("no unexpired linux SDK artifact to choose from; build one first")
    options = [(f"{a.name}  run {a.run_id}  expires {a.expires_at}", a.name) for a in choices]
    return str(choose("Select an SDK artifact", options))


def pick_suites() -> tuple[str, ...]:
    selected = _answer(
        questionary.checkbox(
            "Select the suites to run",
            choices=[questionary.Choice(title=name, value=name) for name in SUITE_NAMES],
            validate=lambda picked: bool(picked) or "select at least one suite",
        ),
        "suites",
    )
    return tuple(str(name) for name in selected)


def pick_run(runs: Sequence[dict[str, Any]]) -> str:
    options = [
        (
            f"{r.get('databaseId')}  {r.get('workflowName')}  {r.get('status')}  {r.get('createdAt')}",
            str(r.get("databaseId")),
        )
        for r in runs
    ]
    return str(choose("Select a run", options))
