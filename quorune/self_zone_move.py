from __future__ import annotations

"""Typed source-self activated zone movement."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol
import re

from .abilities import ActivatedAbility
from .activation_condition_model import activation_restriction_spec
from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .replacement.immutable import FrozenMap, freeze_value
from .entry_counter_model import EffectEntryCounter
from .source_self_entry_counter_model import (
    FIELD as SELF_ENTRY_COUNTER_FIELD, FixedSelfEntryCounter, compile_source_self_entry_counter,
)
from .self_zone_move_followups import (
    SelfEntryFollowup, compile_self_entry_followup, FIELD as SELF_ENTRY_FOLLOWUP_FIELD,
)


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
    followup: SelfEntryFollowup | None = None
    entry_counter: FixedSelfEntryCounter | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise SelfZoneMoveError("Unsupported self-zone-move schema version")
        if ((self.schema_version == 1 and self.followup is not None)
                or (self.schema_version == 2 and not isinstance(self.followup, SelfEntryFollowup))
                or (self.schema_version == 3 and self.followup is not None)):
            raise SelfZoneMoveError('Self-entry follow-ups require their explicit version-two spec')
        if ((self.schema_version != 3 and self.entry_counter is not None)
                or (self.schema_version == 3 and not isinstance(self.entry_counter, FixedSelfEntryCounter))):
            raise SelfZoneMoveError('Source entry counters require their explicit version-three spec')
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
        if self.schema_version == 2:
            material = _self_zone_effect_material(self.ability)
            movement, separator, continuation = material.partition('. ')
            if (not separator or compile_self_entry_followup(continuation) is not self.followup
                    or self.origin != 'graveyard' or self.destination != 'battlefield'
                    or self.source_form != 'card' or movement+'.' != (
                        'Return this card from your graveyard to the battlefield'
                        + (' tapped.' if self.tapped else '.'))):
                raise SelfZoneMoveError('Self-entry continuation does not match its complete printed body')
        if self.schema_version == 3 and (
                compile_source_self_entry_counter(_self_zone_effect_material(self.ability))
                != (self.tapped, self.entry_counter)
                or (self.origin, self.destination, self.source_form) != ('graveyard', 'battlefield', 'card')):
            raise SelfZoneMoveError('Source entry counters do not match the complete printed body')

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SelfZoneMoveSpec":
        if not isinstance(value, Mapping):
            raise SelfZoneMoveError('Self-zone movement spec must be an object')
        expected = {
            "schema_version",
            "ability",
            "origin",
            "destination",
            "tapped",
            "source_form",
            "requires_complete_card_program",
        }
        if value.get('schema_version') == 2:
            expected.add('followup')
        if value.get('schema_version') == 3:
            expected.add('entry_counter')
        if set(value) != expected or not isinstance(value.get("ability"), Mapping):
            raise SelfZoneMoveError("Self-zone movement descriptors have a closed shape")
        try:
            followup = SelfEntryFollowup(value['followup']) if 'followup' in value else None
            entry_counter = (FixedSelfEntryCounter.from_dict(value['entry_counter'])
                             if 'entry_counter' in value else None)
        except (ValueError, TypeError) as exc:
            raise SelfZoneMoveError('Unsupported self-entry follow-up') from exc
        return cls(
            ability=ActivatedAbility.from_dict(value["ability"]),
            origin=value["origin"],
            destination=value["destination"],
            tapped=value["tapped"],
            source_form=value["source_form"],
            requires_complete_card_program=value["requires_complete_card_program"],
            schema_version=value["schema_version"],
            followup=followup,
            entry_counter=entry_counter,
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
            **({'followup': self.followup.value} if self.followup is not None else {}),
            **({'entry_counter': self.entry_counter.to_dict()} if self.entry_counter is not None else {}),
        }

    def effect(self) -> dict[str, Any]:
        return {
            "op": SELF_ZONE_MOVE_OPERATION,
            "origin": self.origin,
            "destination": self.destination,
            "tapped": self.tapped,
            "source_form": self.source_form,
            **({SELF_ENTRY_FOLLOWUP_FIELD: {'version': 1, 'kind': self.followup.value}}
               if self.followup is not None else {}),
            **({SELF_ENTRY_COUNTER_FIELD: self.entry_counter.to_dict()} if self.entry_counter is not None else {}),
        }

    def to_activated_ability(self) -> ActivatedAbility:
        return self.ability


def _self_zone_effect_material(ability: ActivatedAbility) -> str:
    effect = " ".join(ability.effect_text.split())
    restricted = re.fullmatch(r"(?P<effect>.+?\.) Activate only (?P<restriction>.+)\.", effect)
    if restricted is not None:
        restriction = activation_restriction_spec(restricted['restriction'])
        if restriction is None or (
            ability.sorcery_speed is not restriction.sorcery_speed
            or ability.activation_limit is not restriction.activation_limit
            or ability.activation_conditions != restriction.conditions
        ):
            return ''
        effect = restricted['effect']
    return effect


def compile_self_zone_move(
    ability: ActivatedAbility,
) -> SelfZoneMoveSpec | None:
    effect = _self_zone_effect_material(ability)
    counter_entry = compile_source_self_entry_counter(effect)
    if counter_entry is not None:
        tapped, entry_counter = counter_entry
        return SelfZoneMoveSpec(ability=replace(ability, zones=('graveyard',)),
            origin='graveyard', destination='battlefield', tapped=tapped, source_form='card',
            requires_complete_card_program=True, schema_version=3, entry_counter=entry_counter)
    followup = None
    movement, separator, continuation = effect.partition('. ')
    if separator:
        followup = compile_self_entry_followup(continuation)
        if followup is None:
            return None
        effect = movement+'.'
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
    if followup is not None and (origin, destination, source_form) != ('graveyard', 'battlefield', 'card'):
        return None
    return SelfZoneMoveSpec(
        ability=replace(ability, zones=(origin,)),
        origin=origin,
        destination=destination,
        tapped=tapped,
        source_form=source_form,
        requires_complete_card_program=destination == "battlefield",
        schema_version=2 if followup is not None else 1,
        followup=followup,
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
    aura_target_ref: str | None = None
    effect_entry_counters: tuple[EffectEntryCounter, ...] = ()
    aura_target_logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.effect_entry_counters, tuple)
                or any(not isinstance(counter, EffectEntryCounter) for counter in self.effect_entry_counters)
                or self.effect_entry_counters and (
                    len(self.effect_entry_counters) != 1
                    or (self.origin, self.destination, self.source_form) != ('graveyard', 'battlefield', 'card')
                    or self.effect_entry_counters[0].placing_player != self.actor
                    or self.effect_entry_counters[0].source_ref != self.stack_ref)):
            raise SelfZoneMoveError('Self-zone entry counters require their typed placing player and stack source')
        if self.aura_target_ref is not None and (
            self.destination != 'battlefield' or type(self.aura_target_ref) is not str or not self.aura_target_ref
        ):
            raise SelfZoneMoveError('Self-zone Aura entry selection requires a battlefield destination and reference')
        if self.aura_target_logical_object_id is not None and (
            type(self.aura_target_logical_object_id) is not str or not self.aura_target_logical_object_id
            or self.aura_target_ref is None or (self.origin, self.destination, self.source_form) != ('graveyard', 'battlefield', 'card')
        ):
            raise SelfZoneMoveError('Occurrence-bound Aura entry requires its exact graveyard source and battlefield recipient')
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

    def move_object_intent(self, intent: Any) -> Any: ...

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
    if intent.aura_target_logical_object_id is not None and (
        card.owner != intent.actor or not any(
            recipient.ref == intent.aura_target_ref and recipient.logical_object_id == intent.aura_target_logical_object_id
            and recipient.zone == "battlefield" and not recipient.phased_out for recipient in host.state.cards.values()
        )
    ):
        return None
    if intent.effect_entry_counters:
        from .semantic_runtime.zone_move_intent import ZoneMoveIntent
        host.move_object_intent(ZoneMoveIntent(actor=intent.actor, object_ref=card.ref,
            expected_zones=(intent.origin,), destination=intent.destination,
            reason='Activated self-zone movement resolved', new_controller=intent.actor,
            tapped_policy='tapped' if intent.tapped else 'untapped', optional_if_missing=True,
            expected_zone_change_counter=card.zone_change_counter,
            effect_entry_counters=intent.effect_entry_counters,
            replacement_selections=intent.replacement_selections))
        return card.ref if card.zone == intent.destination else None
    host.move_card(
        card.object_id,
        intent.destination,
        controller=intent.actor if intent.destination == "battlefield" else None,
        putting_player=intent.actor if intent.destination == "battlefield" else None,
        tapped=intent.tapped if intent.destination == "battlefield" else None,
        reason="Activated self-zone movement resolved",
        semantic_events=True,
        **({'aura_target_ref':intent.aura_target_ref} if intent.aura_target_ref is not None else {}),
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
