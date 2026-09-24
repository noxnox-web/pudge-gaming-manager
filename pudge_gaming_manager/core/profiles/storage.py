"""Loading and saving Golden Profiles.

A profile arrives on a USB stick or a network share, so loading is a trust
boundary (rule #56): the file is size-limited, parsed as plain JSON and
validated against the schema before a single value is used.
"""

from __future__ import annotations

import json
import pathlib

from pydantic import ValidationError

from ...utilities.exceptions import ProfileSchemaError
from ...utilities.logging_setup import get_logger
from .schema import GoldenProfile

_log = get_logger(__name__)

PROFILE_FILENAME = "pudge_club_profile.json"

#: Refuse absurdly large files before parsing. A profile is a few kilobytes;
#: anything larger is not one, and JSON parsing is not a good place to meet
#: a hostile input.
MAX_PROFILE_BYTES = 256 * 1024


def save(profile: GoldenProfile, path: pathlib.Path | str) -> pathlib.Path:
    """Write a profile as JSON."""
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(profile.model_dump(mode="json"), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _log.info("profile '%s' exported to %s", profile.name, target)
    return target


def load(path: pathlib.Path | str) -> GoldenProfile:
    """Read and validate a profile.

    The file is untrusted. It is size-limited, parsed as plain JSON, and
    validated against the schema with unknown fields rejected. Nothing in
    it is executed or used as a path.
    """
    source = pathlib.Path(path)

    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ProfileSchemaError(
            str(source), f"The file could not be opened: {exc.strerror}."
        ) from exc

    if size > MAX_PROFILE_BYTES:
        raise ProfileSchemaError(
            str(source),
            f"The file is {size} bytes; a profile is expected to be under "
            f"{MAX_PROFILE_BYTES} bytes.",
        )

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileSchemaError(
            str(source), f"The file is not valid JSON (line {exc.lineno})."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ProfileSchemaError(
            str(source), "The file is not UTF-8 text, so it is not a profile."
        ) from exc
    except RecursionError as exc:
        # A hostile file can nest arrays deeply enough to exhaust the parser's
        # stack while staying well under the size limit.
        raise ProfileSchemaError(
            str(source), "The file is nested too deeply to be a profile."
        ) from exc
    except OSError as exc:
        raise ProfileSchemaError(
            str(source), f"The file could not be read: {exc.strerror}."
        ) from exc

    if not isinstance(raw, dict):
        raise ProfileSchemaError(
            str(source), "The file does not contain a profile object."
        )

    try:
        return GoldenProfile.model_validate(raw)
    except ValidationError as exc:
        raise ProfileSchemaError(str(source), _describe(exc)) from exc


def _describe(error: ValidationError) -> str:
    """Turn pydantic's report into something an administrator can act on."""
    problems: list[str] = []
    for item in error.errors()[:5]:
        location = ".".join(str(p) for p in item["loc"]) or "profile"
        problems.append(f"{location}: {item['msg']}")
    if error.error_count() > 5:
        problems.append(f"…and {error.error_count() - 5} more")
    return "; ".join(problems)
