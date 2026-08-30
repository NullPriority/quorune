from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ...morph import FACE_DOWN_CAST_METHODS

from ..action_proposals import (
    ActionOffer,
    CastCostOption,
    CastProposal,
    FrozenArray,
    FrozenJson,
    FrozenObject,
    freeze_json,
    thaw_json,
)


CastProposalStatus = Literal[
    "payable", "unpayable", "unavailable", "unresolved"
]


class CastProposalError(ValueError):
    """A cast request cannot produce a currently executable proposal."""

    def __init__(
        self,
        message: str,
        *,
        status: CastProposalStatus = "unavailable",
        reason: str = "illegal_cast",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


@dataclass(frozen=True, slots=True)
class CastProposalRequest:
    actor: str
    card_ref: str
    zones: tuple[str, ...]
    face: str | None = None
    cast_method: str | None = None
    x_value: int | None = None
    modes: tuple[str, ...] = ()
    targets: FrozenJson = field(default_factory=FrozenObject)
    cost_option_id: str | None = None
    submission: FrozenJson = field(default_factory=FrozenObject)
    authorized_from_zone: str | None = None
    required_face: str | None = None
    force_without_mana_cost: bool = False
    ignore_priority: bool = False
    ignore_timing: bool = False
    during_resolution: bool = False
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CastProposalError("Unsupported cast-request schema version")
        object.__setattr__(
            self, "zones", tuple(str(value) for value in self.zones)
        )
        object.__setattr__(
            self, "modes", tuple(str(value) for value in self.modes)
        )
        if not self.actor or not self.card_ref or not self.zones:
            raise CastProposalError(
                "Cast requests require an actor, card, and source zone"
            )
        if self.cast_method is not None and self.cast_method not in FACE_DOWN_CAST_METHODS:
            raise CastProposalError("Unsupported cast method")
        if not isinstance(self.targets, (FrozenObject, FrozenArray)):
            object.__setattr__(self, "targets", freeze_json(self.targets))
        if not isinstance(self.targets, (FrozenObject, FrozenArray)):
            raise CastProposalError("Cast targets must be an object or array")
        if not isinstance(self.submission, FrozenObject):
            object.__setattr__(self, "submission", freeze_json(self.submission))
        if not isinstance(self.submission, FrozenObject):
            raise CastProposalError("Cast submission must be an object")

    @classmethod
    def from_submission(
        cls,
        actor: str,
        response: Mapping[str, Any],
        *,
        authorized_from_zone: str | None = None,
        required_face: str | None = None,
        force_without_mana_cost: bool = False,
        ignore_priority: bool = False,
        ignore_timing: bool = False,
        during_resolution: bool = False,
    ) -> "CastProposalRequest":
        raw_from = response.get("from")
        if raw_from is None:
            zones = ("hand", "command")
        elif isinstance(raw_from, str):
            zones = (raw_from,)
        elif isinstance(raw_from, Sequence) and not isinstance(
            raw_from, (bytes, bytearray)
        ):
            zones = tuple(str(value) for value in raw_from)
        else:
            raise CastProposalError("Cast source zone must be a string or array")
        raw_x = response.get("x")
        try:
            x_value = int(raw_x) if raw_x is not None else None
        except (TypeError, ValueError) as exc:
            raise CastProposalError("Cast X value must be an integer") from exc
        if x_value is not None and x_value < 0:
            raise CastProposalError("Cast X value must be nonnegative")
        raw_modes = response.get("modes") or ()
        if isinstance(raw_modes, (str, bytes, bytearray)) or not isinstance(
            raw_modes, Sequence
        ):
            raise CastProposalError("Cast modes must be an array")
        return cls(
            actor=actor,
            card_ref=str(response.get("card") or response.get("id") or ""),
            zones=zones,
            face=(str(response["face"]) if response.get("face") else None),
            cast_method=(
                str(response["cast_method"])
                if response.get("cast_method")
                else None
            ),
            x_value=x_value,
            modes=tuple(str(value) for value in raw_modes),
            targets=freeze_json(response.get("targets") or {}),
            cost_option_id=(
                str(response["cost_option"])
                if response.get("cost_option")
                else None
            ),
            submission=freeze_json(dict(response)),
            authorized_from_zone=authorized_from_zone,
            required_face=required_face,
            force_without_mana_cost=force_without_mana_cost,
            ignore_priority=ignore_priority,
            ignore_timing=ignore_timing,
            during_resolution=during_resolution,
        )

    def response(self) -> dict[str, Any]:
        return dict(thaw_json(self.submission))


@dataclass(frozen=True, slots=True)
class CastProposalResult:
    status: CastProposalStatus
    reason: str
    proposal: CastProposal | None = None
    offer: ActionOffer | None = None
    cost_options: tuple[CastCostOption, ...] = ()

    def __post_init__(self) -> None:
        if self.status == "payable" and not (self.proposal or self.offer):
            raise CastProposalError(
                "A payable cast result requires a proposal or offer"
            )
        if self.status != "payable" and (self.proposal or self.offer):
            raise CastProposalError(
                "A rejected cast result cannot carry an executable value"
            )


__all__ = [
    "CastProposalError",
    "CastProposalRequest",
    "CastProposalResult",
    "CastProposalStatus",
]
