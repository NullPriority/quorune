from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ..action_proposals import (
    ActionOffer,
    ActivationProposal,
    FrozenArray,
    FrozenJson,
    FrozenObject,
    freeze_json,
    thaw_json,
)


ActivationProposalStatus = Literal[
    "payable", "unpayable", "unavailable", "unresolved"
]


class ActivationProposalError(ValueError):
    """An activation request cannot produce an executable proposal."""

    def __init__(
        self,
        message: str,
        *,
        status: ActivationProposalStatus = "unavailable",
        reason: str = "illegal_activation",
    ) -> None:
        super().__init__(message)
        self.status = status
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ActivationProposalRequest:
    actor: str
    source_ref: str
    zones: tuple[str, ...]
    ability_selector: str | int | None
    modes: tuple[str, ...] = ()
    targets: FrozenJson = field(default_factory=FrozenObject)
    submission: FrozenJson = field(default_factory=FrozenObject)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ActivationProposalError(
                "Unsupported activation-request schema version"
            )
        object.__setattr__(
            self, "zones", tuple(str(value) for value in self.zones)
        )
        object.__setattr__(
            self, "modes", tuple(str(value) for value in self.modes)
        )
        if not self.actor or not self.source_ref or not self.zones:
            raise ActivationProposalError(
                "Activation requests require an actor, source, and zone"
            )
        if not isinstance(self.targets, (FrozenObject, FrozenArray)):
            object.__setattr__(self, "targets", freeze_json(self.targets))
        if not isinstance(self.targets, (FrozenObject, FrozenArray)):
            raise ActivationProposalError(
                "Activation targets must be an object or array"
            )
        if not isinstance(self.submission, FrozenObject):
            object.__setattr__(self, "submission", freeze_json(self.submission))
        if not isinstance(self.submission, FrozenObject):
            raise ActivationProposalError("Activation submission must be an object")

    @classmethod
    def from_submission(
        cls, actor: str, response: Mapping[str, Any]
    ) -> "ActivationProposalRequest":
        raw_from = response.get("from")
        if raw_from is None:
            zones = ("battlefield", "hand", "graveyard", "exile")
        elif isinstance(raw_from, str):
            zones = (raw_from,)
        elif isinstance(raw_from, Sequence) and not isinstance(
            raw_from, (bytes, bytearray)
        ):
            zones = tuple(str(value) for value in raw_from)
        else:
            raise ActivationProposalError(
                "Activation source zone must be a string or array"
            )
        raw_modes = response.get("modes") or ()
        if isinstance(raw_modes, (str, bytes, bytearray)) or not isinstance(
            raw_modes, Sequence
        ):
            raise ActivationProposalError("Activation modes must be an array")
        selector = response.get("ability", response.get("ability_index"))
        return cls(
            actor=actor,
            source_ref=str(response.get("source") or response.get("id") or ""),
            zones=zones,
            ability_selector=(
                selector
                if isinstance(selector, (str, int)) or selector is None
                else str(selector)
            ),
            modes=tuple(str(value) for value in raw_modes),
            targets=freeze_json(response.get("targets") or {}),
            submission=freeze_json(dict(response)),
        )

    def response(self) -> dict[str, Any]:
        return dict(thaw_json(self.submission))


@dataclass(frozen=True, slots=True)
class ActivationProposalResult:
    status: ActivationProposalStatus
    reason: str
    proposal: ActivationProposal | None = None
    offer: ActionOffer | None = None
    random_target_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.random_target_refs and self.offer is None:
            raise ActivationProposalError("Only a payable offer can retain a random target group")
        if self.status == "payable" and not (self.proposal or self.offer):
            raise ActivationProposalError(
                "A payable activation result needs a proposal or offer"
            )
        if self.status != "payable" and (self.proposal or self.offer):
            raise ActivationProposalError(
                "A rejected activation result cannot be executable"
            )


__all__ = [
    "ActivationProposalError",
    "ActivationProposalRequest",
    "ActivationProposalResult",
    "ActivationProposalStatus",
]
