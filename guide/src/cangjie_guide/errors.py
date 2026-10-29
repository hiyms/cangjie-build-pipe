from __future__ import annotations


class GuideError(RuntimeError):
    """A condition the operator has to resolve; not retried, not papered over."""
