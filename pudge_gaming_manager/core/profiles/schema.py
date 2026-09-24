"""Golden Profile schema.

A profile arrives on a USB stick or a network share. It is **untrusted
input** and is validated before a single value is read (rule #56).

What a profile can and cannot contain
-------------------------------------
It contains *expectations and documented setting keys only*. It cannot
contain a command, a script, an executable path, or a registry path — the
registry keys PGM will touch are fixed in code and a profile can only
select among them. ``extra="forbid"`` rejects any field not defined here,
so a profile from a newer build fails loudly instead of being partially
applied.

Every section is optional. A profile that only pins the power plan is a
valid profile, and the sections it omits are simply not checked.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = 1

_GUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

#: Service startup types a profile may request. ``Disabled`` is absent by
#: design: rule #52 requires a per-service rationale, and a profile file is
#: not a place to disable Windows services wholesale.
AllowedStartupType = Literal["Automatic", "AutomaticDelayedStart", "Manual"]


class _Strict(BaseModel):
    """Base for every profile section: unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class HardwareExpectations(_Strict):
    """What the club considers an acceptable machine.

    These are checked, never enforced — PGM cannot install RAM.
    """

    min_ram_gb: Annotated[int, Field(ge=1, le=1024)] | None = None
    min_vram_gb: Annotated[int, Field(ge=1, le=256)] | None = None
    min_free_disk_percent: Annotated[float, Field(ge=0, le=99)] | None = None
    require_ssd_system_disk: bool | None = None
    gpu_vendor: Literal["NVIDIA", "AMD", "Intel"] | None = None
    min_cpu_cores: Annotated[int, Field(ge=1, le=256)] | None = None


class PowerPolicy(_Strict):
    """The power scheme every PC in the club should run."""

    scheme_guid: str | None = None
    scheme_name: str = ""
    """Recorded for display only. The GUID is what is matched."""

    @field_validator("scheme_guid")
    @classmethod
    def _valid_guid(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _GUID.match(value):
            raise ValueError(
                "scheme_guid must be a GUID such as "
                "381b4222-f694-41f0-9685-ff5bb260df2e"
            )
        return value.lower()


class DisplayPolicy(_Strict):
    """What the display should be doing."""

    require_maximum_refresh_rate: bool = True
    """Flag any display running below its maximum at the current resolution."""

    minimum_refresh_hz: Annotated[int, Field(ge=24, le=1000)] | None = None
    """Flag any display below this rate, whatever its capability."""


class CleanupPolicy(_Strict):
    """Which cleanup categories this club runs."""

    enabled_categories: tuple[str, ...] = ()

    @field_validator("enabled_categories")
    @classmethod
    def _known_categories(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        # Imported lazily: the rules module is a sibling subsystem and this
        # keeps the schema importable on its own.
        from ...windows.cleanup.categories import CATEGORIES_BY_ID

        unknown = [c for c in value if c not in CATEGORIES_BY_ID]
        if unknown:
            raise ValueError(
                "unknown cleanup categories: " + ", ".join(sorted(unknown))
            )
        return value


class ServiceExpectation(_Strict):
    """How one service should be configured."""

    startup_type: AllowedStartupType


class ServicePolicy(_Strict):
    """Expected service configuration, by service name."""

    expected: dict[str, ServiceExpectation] = Field(default_factory=dict)

    @field_validator("expected")
    @classmethod
    def _not_protected(
        cls, value: dict[str, ServiceExpectation]
    ) -> dict[str, ServiceExpectation]:
        """A profile may not target a protected service.

        Without this, a profile file could reconfigure Defender or
        anti-cheat, which is exactly the authority a config file must not
        have (rule #47).
        """
        from ...windows.services.manager import PROTECTED_SERVICES

        protected = [n for n in value if n.lower() in PROTECTED_SERVICES]
        if protected:
            raise ValueError(
                "these services are protected and cannot be set by a "
                "profile: " + ", ".join(sorted(protected))
            )
        return value


class GamesPolicy(_Strict):
    """Which Steam games a club reset must keep.

    A reset removes every installed game *except* these app IDs. Storing the
    keep-list here means the reference PC's exceptions travel with the
    profile, the same way the power plan and display policy do.
    """

    keep_steam_app_ids: tuple[Annotated[int, Field(ge=1, le=2**32)], ...] = ()

    @field_validator("keep_steam_app_ids")
    @classmethod
    def _unique(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        # De-duplicated and ordered so two profiles listing the same games
        # compare equal and the preview is stable.
        return tuple(sorted(set(value)))


class ProfileOrigin(_Strict):
    """Where the profile was captured. Informational."""

    machine_name: str = ""
    os_caption: str = ""
    os_build: str = ""
    gpu_model: str = ""
    captured_at: str = ""
    tool_version: str = ""


class GoldenProfile(_Strict):
    """A club's reference configuration."""

    schema_version: int = SCHEMA_VERSION
    name: Annotated[str, Field(min_length=1, max_length=120)] = "Club profile"
    description: Annotated[str, Field(max_length=2000)] = ""
    origin: ProfileOrigin = Field(default_factory=ProfileOrigin)

    hardware: HardwareExpectations = Field(default_factory=HardwareExpectations)
    power: PowerPolicy = Field(default_factory=PowerPolicy)
    display: DisplayPolicy = Field(default_factory=DisplayPolicy)
    cleanup: CleanupPolicy = Field(default_factory=CleanupPolicy)
    services: ServicePolicy = Field(default_factory=ServicePolicy)
    games: GamesPolicy = Field(default_factory=GamesPolicy)

    @field_validator("schema_version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(
                f"this profile uses schema version {value}, but this build "
                f"supports version {SCHEMA_VERSION}"
            )
        return value

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        """The name reaches file paths and the UI, so keep it boring."""
        if any(ch in value for ch in '\\/:*?"<>|\n\r\t'):
            raise ValueError("name must not contain path or control characters")
        return value
