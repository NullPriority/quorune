from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from typing import Any, Literal, TypeAlias


class ActionProposalError(ValueError):
    """An action proposal or offer is malformed or noncanonical."""


FrozenScalar: TypeAlias = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class FrozenArray:
    items: tuple["FrozenJson", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "items",
            tuple(freeze_json(item) for item in self.items),
        )


@dataclass(frozen=True, slots=True)
class FrozenObject:
    entries: tuple[tuple[str, "FrozenJson"], ...] = ()

    def __post_init__(self) -> None:
        normalized: list[tuple[str, FrozenJson]] = []
        seen: set[str] = set()
        for key, value in self.entries:
            if not isinstance(key, str):
                raise ActionProposalError(
                    "Action proposal object keys must be strings"
                )
            if key in seen:
                raise ActionProposalError(
                    "Action proposal object keys must be unique"
                )
            seen.add(key)
            normalized.append((key, freeze_json(value)))
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(normalized, key=lambda entry: entry[0])),
        )


FrozenJson: TypeAlias = FrozenScalar | FrozenArray | FrozenObject


def freeze_json(value: Any) -> FrozenJson:
    """Deep-copy JSON-compatible data into an immutable canonical value."""

    if isinstance(value, FrozenObject):
        return FrozenObject(value.entries)
    if isinstance(value, FrozenArray):
        return FrozenArray(value.items)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ActionProposalError(
                "Action proposal numbers must be finite"
            )
        return value
    if isinstance(value, Mapping):
        entries: list[tuple[str, FrozenJson]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ActionProposalError(
                    "Action proposal object keys must be strings"
                )
            entries.append((key, freeze_json(item)))
        return FrozenObject(tuple(sorted(entries, key=lambda entry: entry[0])))
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return FrozenArray(tuple(freeze_json(item) for item in value))
    raise ActionProposalError(
        f"Action proposal values must be JSON-compatible, got {type(value).__name__}"
    )


def thaw_json(value: FrozenJson) -> Any:
    """Return an isolated JSON-compatible copy of a frozen value."""

    if isinstance(value, FrozenObject):
        return {key: thaw_json(item) for key, item in value.entries}
    if isinstance(value, FrozenArray):
        return [thaw_json(item) for item in value.items]
    if not isinstance(value, (FrozenObject, FrozenArray)):
        return value
    raise ActionProposalError("Unknown frozen JSON value")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def action_offer_signature_facts(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable rules facts for meaningful-action comparisons.

    Offer revision and fingerprint fields protect command submission, but they
    are transport freshness metadata rather than a change in the action a
    player can take.  Excluding them prevents an unrelated state revision from
    invalidating an otherwise safe yield.
    """

    return {
        key: thaw_json(freeze_json(item))
        for key, item in value.items()
        if key not in {"expiry_revision", "proposal_fingerprint"}
    }


@dataclass(frozen=True, slots=True)
class ActionOffer:
    """One immutable, principal-scoped executable action advertisement."""

    action_id: str
    action: Literal[
        "cast",
        "activate",
        "play_land",
        "turn_face_up",
        "suspend",
        "stage_cast_lifecycle",
        "concede",
        "mana_undo",
    ]
    seat: str
    label: str
    expiry_revision: int = 0
    payload: FrozenJson = field(default_factory=FrozenObject)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ActionProposalError("Unsupported action-offer schema version")
        if self.action not in {
            "cast",
            "activate",
            "play_land",
            "turn_face_up",
            "suspend",
            "stage_cast_lifecycle",
            "concede",
            "mana_undo",
        }:
            raise ActionProposalError("Unsupported action-offer action")
        if not self.action_id or not self.seat or not self.label:
            raise ActionProposalError(
                "Action offers require an ID, seat, and label"
            )
        if type(self.expiry_revision) is not int or self.expiry_revision < 0:
            raise ActionProposalError(
                "Action-offer expiry revisions must be nonnegative integers"
            )
        if not isinstance(self.payload, FrozenObject):
            object.__setattr__(self, "payload", freeze_json(self.payload))
        if not isinstance(self.payload, FrozenObject):
            raise ActionProposalError("Action-offer payload must be an object")

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(self.to_dict(include_fingerprint=False))

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "id": self.action_id,
            "action": self.action,
            "seat": self.seat,
            "label": self.label,
            "expiry_revision": self.expiry_revision,
            **dict(thaw_json(self.payload)),
        }
        if include_fingerprint:
            result["proposal_fingerprint"] = self.fingerprint
        return result


@dataclass(frozen=True, slots=True)
class CastCostOption:
    """One server-authoritative, currently payable casting-cost choice."""

    option_id: str
    kind: str
    requirements: FrozenJson
    payload: FrozenJson = field(default_factory=FrozenObject)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ActionProposalError("Unsupported cast-cost schema version")
        if not self.option_id or not self.kind:
            raise ActionProposalError("Cast-cost options require an ID and kind")
        if not isinstance(self.requirements, FrozenObject):
            object.__setattr__(
                self, "requirements", freeze_json(self.requirements)
            )
        if not isinstance(self.requirements, FrozenObject):
            raise ActionProposalError("Cast-cost requirements must be an object")
        requirements = dict(thaw_json(self.requirements))
        unknown = set(requirements).difference(
            {"GENERIC", "W", "U", "B", "R", "G", "C"}
        )
        if unknown or any(
            type(amount) is not int or amount < 0
            for amount in requirements.values()
        ):
            raise ActionProposalError(
                "Cast-cost requirements must be nonnegative supported mana amounts"
            )
        if not isinstance(self.payload, FrozenObject):
            object.__setattr__(self, "payload", freeze_json(self.payload))
        if not isinstance(self.payload, FrozenObject):
            raise ActionProposalError("Cast-cost payload must be an object")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CastCostOption":
        option_id = str(value.get("id") or "")
        kind = str(value.get("kind") or "")
        requirements = value.get("requirements")
        payload = {
            key: item
            for key, item in value.items()
            if key not in {"id", "kind", "requirements"}
        }
        return cls(
            option_id=option_id,
            kind=kind,
            requirements=freeze_json(requirements),
            payload=freeze_json(payload),
        )

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.option_id,
            "kind": self.kind,
            "requirements": thaw_json(self.requirements),
            **dict(thaw_json(self.payload)),
        }


@dataclass(frozen=True, slots=True)
class CastProposal:
    """Validated casting facts shared by advertisement and execution."""

    seat: str
    card_ref: str
    object_id: str
    origin: str
    face: str | None
    type_line: str
    semantic_key: str
    cost_option_id: str
    requirements: FrozenJson
    targets: tuple[str, ...] = ()
    target_groups: FrozenJson = field(default_factory=FrozenObject)
    target_snapshots: FrozenJson = field(default_factory=FrozenObject)
    tap_cost_refs: tuple[str, ...] = ()
    details: FrozenJson = field(default_factory=FrozenObject)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ActionProposalError("Unsupported cast-proposal schema version")
        if not all(
            (
                self.seat,
                self.card_ref,
                self.object_id,
                self.origin,
                self.type_line,
                self.semantic_key,
                self.cost_option_id,
            )
        ):
            raise ActionProposalError("Cast proposals require stable identity facts")
        object.__setattr__(
            self, "targets", tuple(str(value) for value in self.targets)
        )
        object.__setattr__(
            self,
            "tap_cost_refs",
            tuple(str(value) for value in self.tap_cost_refs),
        )
        for name in (
            "requirements",
            "target_groups",
            "target_snapshots",
            "details",
        ):
            value = getattr(self, name)
            if not isinstance(value, FrozenObject):
                object.__setattr__(self, name, freeze_json(value))
            if not isinstance(getattr(self, name), FrozenObject):
                raise ActionProposalError(
                    f"Cast-proposal {name} must be an object"
                )

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seat": self.seat,
            "card_ref": self.card_ref,
            "object_id": self.object_id,
            "origin": self.origin,
            "face": self.face,
            "type_line": self.type_line,
            "semantic_key": self.semantic_key,
            "cost_option_id": self.cost_option_id,
            "requirements": thaw_json(self.requirements),
            "targets": list(self.targets),
            "target_groups": thaw_json(self.target_groups),
            "target_snapshots": thaw_json(self.target_snapshots),
            "tap_cost_refs": list(self.tap_cost_refs),
            "details": thaw_json(self.details),
        }


@dataclass(frozen=True, slots=True)
class ActivationProposal:
    """Validated activated-ability facts shared by offers and execution."""

    seat: str
    source_ref: str
    source_object_id: str
    source_zone: str
    ability_id: str
    semantic_key: str | None
    mana_ability: bool
    requirements: FrozenJson
    targets: tuple[str, ...] = ()
    target_groups: FrozenJson = field(default_factory=FrozenObject)
    target_snapshots: FrozenJson = field(default_factory=FrozenObject)
    details: FrozenJson = field(default_factory=FrozenObject)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ActionProposalError(
                "Unsupported activation-proposal schema version"
            )
        if not all(
            (
                self.seat,
                self.source_ref,
                self.source_object_id,
                self.source_zone,
                self.ability_id,
            )
        ):
            raise ActionProposalError(
                "Activation proposals require stable identity facts"
            )
        object.__setattr__(
            self, "targets", tuple(str(value) for value in self.targets)
        )
        for name in (
            "requirements",
            "target_groups",
            "target_snapshots",
            "details",
        ):
            value = getattr(self, name)
            if not isinstance(value, FrozenObject):
                object.__setattr__(self, name, freeze_json(value))
            if not isinstance(getattr(self, name), FrozenObject):
                raise ActionProposalError(
                    f"Activation-proposal {name} must be an object"
                )

    @property
    def fingerprint(self) -> str:
        return _canonical_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "seat": self.seat,
            "source_ref": self.source_ref,
            "source_object_id": self.source_object_id,
            "source_zone": self.source_zone,
            "ability_id": self.ability_id,
            "semantic_key": self.semantic_key,
            "mana_ability": self.mana_ability,
            "requirements": thaw_json(self.requirements),
            "targets": list(self.targets),
            "target_groups": thaw_json(self.target_groups),
            "target_snapshots": thaw_json(self.target_snapshots),
            "details": thaw_json(self.details),
        }


__all__ = [
    "ActionOffer",
    "ActionProposalError",
    "ActivationProposal",
    "CastCostOption",
    "CastProposal",
    "FrozenArray",
    "FrozenObject",
    "action_offer_signature_facts",
    "freeze_json",
    "thaw_json",
]
