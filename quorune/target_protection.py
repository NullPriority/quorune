from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .target_source import TargetingSource
from .qualified_hexproof import qualified_hexproof_prohibits

from .protection import ProtectionVerdict


class TargetProtectionError(ValueError):
    """The immutable target-protection snapshot is malformed."""


class TargetProtectionVerdict(str, Enum):
    ALLOWED = "allowed"
    SOURCE_PROHIBITION = "source_prohibition"
    PLAYER_PROTECTION = "player_protection"
    CONTROLLER_HEXPROOF_FROM_COLOR = "controller_hexproof_from_color"
    SHROUD = "shroud"
    HEXPROOF = "hexproof"
    PROTECTION = "protection"
    UNRESOLVED_PROTECTION = "unresolved_protection"


def _normalized_terms(
    values: frozenset[str],
    *,
    field_name: str,
    upper: bool,
) -> None:
    if not isinstance(values, frozenset):
        raise TargetProtectionError(f"{field_name} must be a frozenset")
    normalize = str.upper if upper else str.casefold
    if any(
        not isinstance(value, str)
        or not value.strip()
        or value != normalize(value.strip())
        for value in values
    ):
        raise TargetProtectionError(f"{field_name} must be canonical")


@dataclass(frozen=True, slots=True)
class TargetProtectionSnapshot:
    """Closed current facts used only for target-protection legality.

    The snapshot does not discover characteristics or mutate game state.  The
    engine adapter supplies current effective keywords and the existing typed
    protection verdict before this pure owner applies targeting restrictions.
    """

    acting_controller: str
    protected_controller: str
    target_is_player: bool = False
    target_keywords: frozenset[str] = frozenset()
    source_colors: frozenset[str] = frozenset()
    controller_hexproof_colors: frozenset[str] = frozenset()
    player_protection_from_everything: bool = False
    protection_verdict: ProtectionVerdict = ProtectionVerdict.ALLOWED
    source_qualities: TargetingSource | None = None

    def __post_init__(self) -> None:
        if self.source_qualities is not None:
            from .target_source import TargetingSource
            if not isinstance(self.source_qualities,TargetingSource):raise TargetProtectionError('Target source qualities must be typed')
        if not isinstance(self.acting_controller, str) or not self.acting_controller:
            raise TargetProtectionError("acting_controller must be nonempty")
        if (
            not isinstance(self.protected_controller, str)
            or not self.protected_controller
        ):
            raise TargetProtectionError("protected_controller must be nonempty")
        if type(self.target_is_player) is not bool:
            raise TargetProtectionError("target_is_player must be boolean")
        if type(self.player_protection_from_everything) is not bool:
            raise TargetProtectionError(
                "player_protection_from_everything must be boolean"
            )
        _normalized_terms(
            self.target_keywords,
            field_name="target_keywords",
            upper=False,
        )
        _normalized_terms(
            self.source_colors,
            field_name="source_colors",
            upper=True,
        )
        _normalized_terms(
            self.controller_hexproof_colors,
            field_name="controller_hexproof_colors",
            upper=True,
        )
        if not isinstance(self.protection_verdict, ProtectionVerdict):
            raise TargetProtectionError(
                "protection_verdict must be a ProtectionVerdict"
            )


def target_protection_verdict(
    snapshot: TargetProtectionSnapshot,
) -> TargetProtectionVerdict:
    """Apply current targeting prohibitions without interpreting Oracle text."""

    if not isinstance(snapshot, TargetProtectionSnapshot):
        raise TargetProtectionError(
            "target protection requires a TargetProtectionSnapshot"
        )
    if snapshot.target_is_player and snapshot.player_protection_from_everything:
        return TargetProtectionVerdict.PLAYER_PROTECTION
    if (
        snapshot.acting_controller != snapshot.protected_controller
        and snapshot.source_colors.intersection(
            snapshot.controller_hexproof_colors
        )
    ):
        return TargetProtectionVerdict.CONTROLLER_HEXPROOF_FROM_COLOR
    if "shroud" in snapshot.target_keywords:
        return TargetProtectionVerdict.SHROUD
    if (
        ("hexproof" in snapshot.target_keywords or qualified_hexproof_prohibits(snapshot.target_keywords,snapshot.source_qualities))
        and snapshot.acting_controller != snapshot.protected_controller
    ):
        return TargetProtectionVerdict.HEXPROOF
    if snapshot.target_is_player:
        return TargetProtectionVerdict.ALLOWED
    if snapshot.protection_verdict is ProtectionVerdict.BLOCKED:
        return TargetProtectionVerdict.PROTECTION
    if snapshot.protection_verdict is ProtectionVerdict.UNRESOLVED:
        return TargetProtectionVerdict.UNRESOLVED_PROTECTION
    return TargetProtectionVerdict.ALLOWED


__all__ = [
    "TargetProtectionError",
    "TargetProtectionSnapshot",
    "TargetProtectionVerdict",
    "target_protection_verdict",
]
