from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label, ListItem, ListView, Static

from cangjie_guide.schema import Artifact


class ChoiceScreen(ModalScreen[Any]):
    """Pick one item from a list, or cancel."""

    def __init__(self, title: str, options: Sequence[tuple[str, Any]]) -> None:
        super().__init__()
        self._title = title
        self._options = list(options)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Header()
            yield Label(self._title)
            yield ListView(
                *[
                    ListItem(Label(label), id=f"opt-{index}")
                    for index, (label, _) in enumerate(self._options)
                ],
                id="choices",
            )
            yield Button("Cancel", id="cancel")
            yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None:
            self.dismiss(None)
            return
        self.dismiss(self._options[index][1])

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Header()
            yield Static(self._question, id="question")
            yield Button("Yes", id="yes", variant="warning")
            yield Button("No", id="no", variant="primary")
            yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class _Prompt(App[Any]):
    SCREEN: type[Any] = ChoiceScreen

    def __init__(self, screen: Any) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


class _ConfirmApp(App[Any]):
    def __init__(self, screen: ConfirmScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def choose(title: str, options: Sequence[tuple[str, Any]]) -> Any:
    app: _Prompt = _Prompt(ChoiceScreen(title, options))
    return app.run()


def confirm(question: str) -> bool:
    app = _ConfirmApp(ConfirmScreen(question))
    return bool(app.run())


def pick_sdk(choices: Sequence[Artifact]) -> str:
    if not choices:
        from cangjie_guide.errors import GuideError

        raise GuideError("no unexpired linux SDK artifact to choose from; build one first")
    options = [(f"{a.name}  run {a.run_id}  expires {a.expires_at}", a.name) for a in choices]
    picked = choose("Select an SDK artifact", options)
    if picked is None:
        from cangjie_guide.errors import GuideError

        raise GuideError("no artifact selected")
    return str(picked)


def pick_suites() -> tuple[str, ...]:
    options = [("llt", "llt"), ("hlt", "hlt"), ("conformance", "conformance")]
    picked = choose("Select a suite", options)
    if picked is None:
        from cangjie_guide.errors import GuideError

        raise GuideError("no suite selected")
    return (str(picked),)


def pick_run(runs: Sequence[dict[str, Any]]) -> str:
    options = [
        (
            f"{r.get('databaseId')}  {r.get('workflowName')}  {r.get('status')}  {r.get('createdAt')}",
            str(r.get("databaseId")),
        )
        for r in runs
    ]
    picked = choose("Select a run", options)
    if picked is None:
        from cangjie_guide.errors import GuideError

        raise GuideError("no run selected")
    return str(picked)
