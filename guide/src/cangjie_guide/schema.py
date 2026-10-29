from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast


@dataclass(frozen=True)
class WorkflowInput:
    name: str
    default: str
    required: bool
    options: tuple[str, ...] = ()
    description: str = ""

    @property
    def is_choice(self) -> bool:
        return bool(self.options)


@dataclass(frozen=True)
class Workflow:
    """One dispatchable workflow, as declared in the repository."""

    file: str
    display_name: str
    ref: str
    inputs: tuple[WorkflowInput, ...] = field(default_factory=tuple)
    secrets: tuple[str, ...] = field(default_factory=tuple)

    def input(self, name: str) -> WorkflowInput | None:
        for item in self.inputs:
            if item.name == name:
                return item
        return None


@dataclass(frozen=True)
class Artifact:
    name: str
    run_id: str
    size_in_bytes: int
    expired: bool
    expires_at: str
    created_at: str

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Artifact:
        run_id = ""
        run: Any = raw.get("workflow_run")
        if isinstance(run, dict):
            run_id = str(cast(dict[str, Any], run).get("id", ""))
        return cls(
            name=str(raw.get("name", "")),
            run_id=run_id,
            size_in_bytes=int(str(raw.get("size_in_bytes", 0))),
            expired=bool(raw.get("expired", False)),
            expires_at=str(raw.get("expires_at", "")),
            created_at=str(raw.get("created_at", "")),
        )
