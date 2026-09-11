from __future__ import annotations

"""Typed source-self activated zone movement."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from .abilities import ActivatedAbility
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .replacement.immutable import FrozenMap, freeze_value


SELF_ZONE_MOVE_CAPABILITY_ID = "zone.self_move.activated"
SELF_ZONE_MOVE_ABILITY_HANDLER_ID = "ability.activated.self-zone-move.v1"
SELF_ZONE_MOVE_EFFECT_HANDLER_ID = "generic.self-zone-move.v1"
SELF_ZONE_MOVE_OPERATION = "self_zone_move"
_SUPPORTED_RESULTS = {
    ("graveyard", "hand", False, "card"),
    ("graveyard", "battlefield", False, "card"),
    ("graveyard", "battlefield", True, "card"),
    ("battlefield", "hand", False, "aura"),
}


class SelfZoneMoveError(ValueError):
    """A source-self zone movement descriptor or intent is malformed."""


@dataclass(frozen=True, slots=True)
class SelfZoneMoveSpec:
    ability: ActivatedAbility
    origin: str
    destination: str
    tapped: bool
    source_form: str
    requires_complete_card_program: bool
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise SelfZoneMoveError("Unsupported self-zone-move schema version")
        if not isinstance(self.ability, ActivatedAbility):
            raise SelfZoneMoveError("Self-zone movement requires a typed ability")
        if any(
            type(value) is not str or not value
            for value in (self.origin, self.destination, self.source_form)
        ) or type(self.tapped) is not bool:
            raise SelfZoneMoveError("Self-zone movement result is malformed")
        result = (self.origin, self.destination, self.tapped, self.source_form)
        if result not in _SUPPORTED_RESULTS:
            raise SelfZoneMoveError("Self-zone movement result is outside the closed family")
        if type(self.requires_complete_card_program) is not bool:
            raise SelfZoneMoveError("Self-zone complete-card policy must be boolean")
        if self.requires_complete_card_program is not (
            self.destination == "battlefield"
        ):
            raise SelfZoneMoveError("Only battlefield self-zone movement requires complete-card admission")
        if self.ability.zones != (self.origin,):
            raise SelfZoneMoveError(
                "Self-zone movement ability has the wrong active zone"
            )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelfZoneMoveSpec":
        expected = {
            "schema_version",
            "ability",
            "origin",
            "destination",
            "tapped",
            "source_form",
            "requires_complete_card_program",
        }
        if set(value) != expected or not isinstance(value.get("ability"), Mapping):
            raise SelfZoneMoveError("Self-zone movement descriptors have a closed shape")
        return cls(
            ability=ActivatedAbility.from_dict(value["ability"]),
            origin=value["origin"],
            destination=value["destination"],
            tapped=value["tapped"],
            source_form=value["source_form"],
            requires_complete_card_program=value["requires_complete_card_program"],
            schema_version=value["schema_version"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ability": self.ability.to_dict(),
            "origin": self.origin,
            "destination": self.destination,
            "tapped": self.tapped,
            "source_form": self.source_form,
            "requires_complete_card_program": self.requires_complete_card_program,
        }

    def effect(self) -> dict[str, Any]:
        return {
            "op": SELF_ZONE_MOVE_OPERATION,
            "origin": self.origin,
            "destination": self.destination,
            "tapped": self.tapped,
            "source_form": self.source_form,
        }

    def to_activated_ability(self) -> ActivatedAbility:
        return self.ability


def compile_self_zone_move(
    ability: ActivatedAbility,
) -> SelfZoneMoveSpec | None:
    effect = " ".join(ability.effect_text.split())
    result: tuple[str, str, bool, str] | None = None
    if effect == "Return this card from your graveyard to your hand.":
        result = ("graveyard", "hand", False, "card")
    elif effect == "Return this card from your graveyard to the battlefield.":
        result = ("graveyard", "battlefield", False, "card")
    elif effect == "Return this card from your graveyard to the battlefield tapped.":
        result = ("graveyard", "battlefield", True, "card")
    elif effect == "Return this Aura to its owner's hand.":
        result = ("battlefield", "hand", False, "aura")
    if result is None:
        return None
    origin, destination, tapped, source_form = result
    return SelfZoneMoveSpec(
        ability=replace(ability, zones=(origin,)),
        origin=origin,
        destination=destination,
        tapped=tapped,
        source_form=source_form,
        requires_complete_card_program=destination == "battlefield",
    )


def self_zone_move_handler_descriptor(spec: SelfZoneMoveSpec) -> dict[str, Any]:
    return {
        "handler_id": SELF_ZONE_MOVE_ABILITY_HANDLER_ID,
        "schema_version": 1,
        "event": "activate",
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: spec.requires_complete_card_program,
        "move": spec.to_dict(),
    }


@dataclass(frozen=True, slots=True)
class SelfZoneMoveIntent:
    actor: str
    stack_ref: str
    object_id: str
    card_ref: str
    logical_object_id: str
    origin: str
    destination: str
    tapped: bool
    source_form: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.stack_ref,
                self.object_id,
                self.card_ref,
                self.logical_object_id,
            )
        ):
            raise SelfZoneMoveError("Self-zone movement requires complete source identity")
        if any(
            type(value) is not str or not value
            for value in (self.origin, self.destination, self.source_form)
        ) or type(self.tapped) is not bool:
            raise SelfZoneMoveError("Self-zone movement intent result is malformed")
        if (self.origin, self.destination, self.tapped, self.source_form) not in _SUPPORTED_RESULTS:
            raise SelfZoneMoveError("Self-zone movement intent is outside the closed family")
        frozen = tuple(
            value
            if isinstance(value, str)
            else freeze_value(value, field="self_zone_move.replacement_selection")
            for value in self.replacement_selections
        )
        if any(not isinstance(value, (str, FrozenMap)) or not value for value in frozen):
            raise SelfZoneMoveError("Self-zone replacement selections are malformed")
        object.__setattr__(self, "replacement_selections", frozen)


class SelfZoneMoveHost(Protocol):
    state: Any

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...


def resolve_self_zone_move(
    host: SelfZoneMoveHost,
    intent: SelfZoneMoveIntent,
) -> str | None:
    if not isinstance(intent, SelfZoneMoveIntent):
        raise SelfZoneMoveError("Self-zone movement requires a typed intent")
    card = host.state.cards.get(intent.object_id)
    if (
        card is None
        or card.ref != intent.card_ref
        or card.logical_object_id != intent.logical_object_id
        or card.zone != intent.origin
        or card.phased_out
        or card.object_kind != "card"
    ):
        return None
    if intent.source_form == "aura":
        _types, subtypes, _supertypes = host._type_parts(
            str(host._effective_card_data(card).get("type_line") or "")
        )
        if "aura" not in subtypes:
            return None
    host.move_card(
        card.object_id,
        intent.destination,
        controller=intent.actor if intent.destination == "battlefield" else None,
        tapped=intent.tapped if intent.destination == "battlefield" else None,
        reason="Activated self-zone movement resolved",
        semantic_events=True,
        replacement_selections=intent.replacement_selections,
    )
    return card.ref if card.zone == intent.destination else None


__all__ = [
    "compile_self_zone_move",
    "resolve_self_zone_move",
    "SelfZoneMoveError",
    "SelfZoneMoveHost",
    "SelfZoneMoveIntent",
    "SelfZoneMoveSpec",
    "self_zone_move_handler_descriptor",
    "SELF_ZONE_MOVE_ABILITY_HANDLER_ID",
    "SELF_ZONE_MOVE_CAPABILITY_ID",
    "SELF_ZONE_MOVE_EFFECT_HANDLER_ID",
    "SELF_ZONE_MOVE_OPERATION",
]
