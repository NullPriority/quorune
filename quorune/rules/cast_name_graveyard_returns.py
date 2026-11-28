from __future__ import annotations

"""Printed same-name returns tied to one normalized creature-spell cast."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..carddb_characteristics import public_stack_rules_names
from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key


CAST_NAME_RETURN_VERSION = 78
CAST_NAMES_FIELD = "cast_spell_names"
CAST_NAME_RETURN_CONTEXT = "cast_name_graveyard_return"
CAST_NAMES_VALUE = "$context." + CAST_NAME_RETURN_CONTEXT
CAST_NAME_RETURN_CAPABILITY = "zone.return.each_graveyard_cast_spell_name"
CAST_NAME_RETURN_MECHANICS = ("fixed-public-zone-move", "fixed-public-zone-move-set")
CAST_CREATURE_CONDITION = FrozenMap({"field": "types", "op": "contains_any", "value": ["creature"]})
GRAVEYARD_CARDS = ObjectQuerySpec(zones=("graveyard",), token=False)


def checked_spell_names(value):
    if (not isinstance(value, (tuple, list))
            or any(type(name) is not str or not name or name.strip() != name or name.startswith("$") for name in value)
            or tuple(value) != tuple(sorted(set(value)))):
        raise ValueError("Cast-name returns require canonical public spell names")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class CastNameReturnSnapshot:
    stack_ref: str
    object_identity: ContinuousObjectIdentity
    card_ref: str
    last_known_names: tuple[str, ...]

    def __post_init__(self):
        if (not isinstance(self.object_identity, ContinuousObjectIdentity)
                or any(type(ref) is not str or not ref or ref.strip() != ref or ref.startswith("$")
                       for ref in (self.stack_ref, self.card_ref))):
            raise ValueError("A cast-name return requires the original public stack object")
        object.__setattr__(self, "last_known_names", checked_spell_names(self.last_known_names))

    def to_dict(self):
        return {
            "schema_version": 1, "stack_ref": self.stack_ref, "object_identity": self.object_identity.to_dict(),
            "card_ref": self.card_ref, "last_known_names": list(self.last_known_names),
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {"schema_version", "stack_ref", "object_identity", "card_ref", "last_known_names"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Cast-name return facts have an invalid shape")
        return cls(value["stack_ref"], ContinuousObjectIdentity.from_dict(value["object_identity"]),
                   value["card_ref"], value["last_known_names"])

    def live_card(self, host):
        stack = next((item for item in host.state.stack if item.ref == self.stack_ref), None)
        if stack is None:
            return None
        card = host.state.cards.get(self.object_identity.object_id)
        if (stack.kind != "spell" or stack.card_object_id != self.object_identity.object_id
                or card is None or card.zone != "stack" or card.ref != self.card_ref
                or card.logical_object_id != self.object_identity.logical_object_id):
            raise ValueError("Cast-name return changed its original spell incarnation")
        return card

    def current_names(self, host):
        card = self.live_card(host)
        return self.last_known_names if card is None else _stack_names(host, card)


def _stack_names(host, card):
    return tuple(sorted(set(public_stack_rules_names(card, host._effective_card_data(card), host.card_record(card)))))


def cast_name_graveyard_return_effect():
    from ..public_zone_moves import (
        PublicBattlefieldController, PublicZoneDestination, PublicZoneMoveSetSpec,
        PublicZoneOrigin, PublicZoneRelationAxis, PublicZoneSeatRelation,
    )

    spec = PublicZoneMoveSetSpec(
        query=GRAVEYARD_CARDS, origin=PublicZoneOrigin.GRAVEYARD, destination=PublicZoneDestination.BATTLEFIELD,
        relation_axis=PublicZoneRelationAxis.OWNER, seat_relation=PublicZoneSeatRelation.ANY,
        battlefield_controller=PublicBattlefieldController.OWNER, schema_version=CAST_NAME_RETURN_VERSION,
        cast_spell_names=CAST_NAMES_VALUE,
    )
    return {"op": "move_public_zone_set", "source": "$source", "set": spec.to_dict()}


def cast_name_return_is_marked(value):
    raw_set = value.get("set") if isinstance(value, Mapping) else None
    return isinstance(raw_set, Mapping) and (CAST_NAMES_FIELD in raw_set or raw_set.get("schema_version") == CAST_NAME_RETURN_VERSION)


def cast_name_graveyard_return_runtime_dependencies(program):
    if (not any(cast_name_return_is_marked(effect) for effect in program.effects)
            and not contains_nested_key((program.effects, program.handlers), CAST_NAMES_FIELD)
            and CAST_NAME_RETURN_CAPABILITY not in program.capability_dependencies):
        return ()
    if ((program.event, program.active_zone) != ("spell.cast", "battlefield")
            or FrozenMap(program.event_condition or {}) != CAST_CREATURE_CONDITION
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.trigger_limit is not None or len(program.effects) != 1
            or FrozenMap(program.effects[0]) != FrozenMap(cast_name_graveyard_return_effect())):
        raise ValueError("Cast-name returns require their full untargeted creature-spell cast trigger")
    return (CAST_NAME_RETURN_CAPABILITY, "trigger.event.normalized_spell_cast",
            "trigger.effect.fixed_event", "trigger.placement.apnap")


def capture_cast_name_graveyard_return(host, program, event, stack_context):
    if not any(cast_name_return_is_marked(effect) for effect in program.effects):
        return {}
    if event != "spell.cast" or "creature" not in stack_context.get("types", ()):
        raise ValueError("Cast-name returns require a committed creature-spell cast")
    cast_name_graveyard_return_runtime_dependencies(program)
    identity = ContinuousObjectIdentity(stack_context["object_id"], stack_context["logical_object_id"])
    frame = CastNameReturnSnapshot(stack_context["stack"], identity, stack_context["card"], ())
    card = frame.live_card(host)
    if card is None or card.controller != stack_context["controller"]:
        raise ValueError("The original cast spell is unavailable at trigger discovery")
    return {CAST_NAME_RETURN_CONTEXT: CastNameReturnSnapshot(
        frame.stack_ref, identity, frame.card_ref, _stack_names(host, card),
    ).to_dict()}


def bind_cast_name_graveyard_return(host, value, item):
    if not cast_name_return_is_marked(value):
        return value
    if (FrozenMap(value) != FrozenMap(cast_name_graveyard_return_effect())
            or item.kind not in {"triggered_ability", "triggered_ability_copy"}
            or item.context.get("currently_resolving") is not True
            or item.context.get("event") != "spell.cast"):
        raise ValueError("The same-name return lost its resolving creature-spell trigger")
    snapshot = CastNameReturnSnapshot.from_dict(item.context.get(CAST_NAME_RETURN_CONTEXT))
    if (snapshot.stack_ref, snapshot.card_ref, snapshot.object_identity.object_id, snapshot.object_identity.logical_object_id) != (
        item.context.get("stack"), item.context.get("card"), item.context.get("object_id"), item.context.get("logical_object_id"),
    ):
        raise ValueError("Stored spell names belong to a different cast occurrence")
    return {**value, "set": {**value["set"], CAST_NAMES_FIELD: snapshot.current_names(host)}}
