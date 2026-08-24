from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .deathtouch import source_has_deathtouch
from .damage_source import DamageError, DamageSourceSnapshot
from .replacement_effects import AffectedObject, ReplaceableEvent


DamageRecipientKind = Literal["player", "permanent"]
_DAMAGE_REASON_FIELD = "".join(("rea", "son"))


@dataclass(frozen=True, slots=True)
class DamageRecipientSnapshot:
    ref: str
    kind: DamageRecipientKind
    controller: str
    object_id: str | None = None
    logical_object_id: str | None = None
    owner: str | None = None
    types: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"player", "permanent"}:
            raise DamageError(
                "Damage recipient kinds must be player or permanent"
            )
        if not self.ref or not self.controller:
            raise DamageError(
                "Damage recipients require stable identity and controller facts"
            )
        if self.kind == "player":
            if any(
                value is not None
                for value in (
                    self.object_id,
                    self.logical_object_id,
                    self.owner,
                )
            ):
                raise DamageError(
                    "Player damage recipients cannot carry object identity"
                )
        elif not all((self.object_id, self.logical_object_id, self.owner)):
            raise DamageError(
                "Permanent damage recipients require complete object identity"
            )
        for field in ("types", "subtypes"):
            raw = getattr(self, field)
            if not isinstance(raw, (list, tuple)) or any(
                type(value) is not str or not value for value in raw
            ):
                raise DamageError(f"Damage recipient {field} are malformed")
            object.__setattr__(self, field, tuple(sorted(set(raw))))

    @property
    def affected_object(self) -> AffectedObject | None:
        if self.kind == "player":
            return None
        assert self.object_id is not None and self.owner is not None
        return AffectedObject(
            object_id=self.object_id,
            owner=self.owner,
            controller=self.controller,
        )


@dataclass(frozen=True, slots=True)
class DamageProposal:
    proposal_id: str
    source: DamageSourceSnapshot
    recipient: DamageRecipientSnapshot
    amount: int
    combat: bool
    reason: str
    unpreventable: bool = False
    damage_step: int | None = None
    first_strike_step: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.source, DamageSourceSnapshot) or not isinstance(
            self.recipient, DamageRecipientSnapshot
        ):
            raise DamageError(
                "Damage proposals require typed source and recipient snapshots"
            )
        if not self.proposal_id or not self.reason:
            raise DamageError("Damage proposals require stable IDs and reasons")
        if type(self.amount) is not int or self.amount < 0:
            raise DamageError("Damage cannot be negative")
        if self.damage_step is not None and self.damage_step < 1:
            raise DamageError("Damage step indexes must be positive")

    def event(self) -> ReplaceableEvent:
        if self.amount < 1:
            raise DamageError("Zero damage has no replaceable event")
        payload = {
            "source": self.source.ref,
            "source_object_id": self.source.object_id,
            "source_logical_object_id": self.source.logical_object_id,
            "source_zone": self.source.zone,
            "source_identity_key": self.source.identity_key,
            "source_oracle_id": self.source.oracle_id,
            "source_commander_designation_id": (
                self.source.commander_designation_id
            ),
            "source_controller": self.source.controller,
            "source_owner": self.source.owner,
            "source_types": list(self.source.types),
            "source_subtypes": list(self.source.subtypes),
            "source_supertypes": list(self.source.supertypes),
            "source_characteristics": sorted(
                {
                    *self.source.types,
                    *self.source.subtypes,
                    *self.source.supertypes,
                    *self.source.keywords,
                }
            ),
            "source_colors": list(self.source.colors),
            "source_keywords": list(self.source.keywords),
            "source_mana_value": self.source.mana_value,
            "source_is_commander": self.source.is_commander,
            "source_toxic_value": self.source.toxic_value,
            "target": self.recipient.ref,
            "target_kind": self.recipient.kind,
            "target_object_id": self.recipient.object_id,
            "target_logical_object_id": self.recipient.logical_object_id,
            "target_controller": self.recipient.controller,
            "target_owner": self.recipient.owner,
            "target_types": list(self.recipient.types),
            "target_subtypes": list(self.recipient.subtypes),
            "target_characteristics": sorted(
                {*self.recipient.types, *self.recipient.subtypes}
            ),
            "proposed_amount": self.amount,
            "amount": self.amount,
            "prevented": 0,
            "combat": self.combat,
            _DAMAGE_REASON_FIELD: self.reason,
            "unpreventable": self.unpreventable,
            # Retain this additive payload field for historical pending-event
            # compatibility, but derive it from the pinned source snapshot.
            "deathtouch": source_has_deathtouch(self.source),
            "damage_step": self.damage_step,
            "first_strike_step": self.first_strike_step,
        }
        return ReplaceableEvent(
            event_id=self.proposal_id,
            kind="damage",
            affected_player=(
                self.recipient.ref
                if self.recipient.kind == "player"
                else None
            ),
            affected_object=self.recipient.affected_object,
            payload=payload,
        )


__all__ = [
    "DamageError",
    "DamageProposal",
    "DamageRecipientKind",
    "DamageRecipientSnapshot",
    "DamageSourceSnapshot",
]
