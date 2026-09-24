"""How a worker-thread failure becomes text for the operator."""

from __future__ import annotations


def readable_error(exc: Exception) -> str:
    """Prefer the what/reason/remedy rendering; never a raw traceback (rule #36)."""
    message = getattr(exc, "user_message", None)
    return message() if callable(message) else str(exc)
