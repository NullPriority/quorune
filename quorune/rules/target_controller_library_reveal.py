"""A legal destruction target determines its controller's matching library prefix."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects


TARGET_CONTROLLER_REVEAL_CAPABILITY = "library.reveal_until.destroy_target_controller_type"
REVEAL_OPERATION = "reveal_until_destroy_target_controller_type"
PARTITION_OPERATION = "place_destroy_target_shared_type_library_prefix"

_FOLLOWUP_KIND = "target_controller_shared_type_library"
_TARGET = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "controller_relation": "opponent",
})
_REQUIRED = (
    TARGET_CONTROLLER_REVEAL_CAPABILITY, "permanent.destroy.effect",
    "target.revalidate_resolution", "library.select.reveal_until_controller",
    "library.select.battlefield_controller",
)


@dataclass(frozen=True, slots=True)
class TargetControllerLibraryReveal:
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("A destruction target's library result needs its exact version")

    def to_dict(self):
        return {"kind": _FOLLOWUP_KIND, "schema_version": self.schema_version}

    @classmethod
    def from_dict(cls, value):
        if not is_target_controller_library_descriptor(value) or set(value) != {"kind", "schema_version"}:
            raise ValueError("A destruction target's library result needs its complete descriptor")
        return cls(value["schema_version"])

    def effect(self):
        return {"op": "destroy", "card": "$target.0", "followup": self.to_dict()}


def is_target_controller_library_descriptor(value):
    return isinstance(value, Mapping) and value.get("kind") == _FOLLOWUP_KIND


def uses_target_controller_library_reveal(effect):
    return effect.get("op") == "destroy" and is_target_controller_library_descriptor(effect.get("followup"))


def target_controller_library_target():
    return thaw_value(_TARGET)


def target_controller_library_dependencies(effect, target_schema):
    if (not uses_target_controller_library_reveal(effect) or set(effect) != {"op", "card", "followup"}
            or effect["card"] != "$target.0" or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int or FrozenMap(target_schema) != _TARGET):
        raise ValueError("The library result requires one ordinary destruction targeting an opponent's permanent")
    TargetControllerLibraryReveal.from_dict(effect["followup"])
    return _REQUIRED


def target_controller_library_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_target_controller_library_reveal(effect)
                   or effect.get("op") in (REVEAL_OPERATION, PARTITION_OPERATION))
    if not marked and TARGET_CONTROLLER_REVEAL_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or tuple(program.effects) != marked or program.handlers
            or program.cost_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("The library result requires its original direct spell instruction")
    return target_controller_library_dependencies(marked[0], program.target_schema)


def target_controller_library_facts(host, card):
    effective = host._effective_card_data(card)
    types, _, _ = host._type_parts(str(effective.get("type_line") or ""))
    return effective.get("controller", card.controller), tuple(sorted(set(types)))


def committed_target_controller_library_result(host, *, target_ref, target_object_id, target_identity,
                                                caster, before):
    current = host.state.cards.get(target_object_id)
    # CR 608.2h uses current facts if destruction was prevented and last known
    # facts if the object left. Neither outcome suppresses the library clause.
    if current is not None and current.zone == "battlefield" and current.logical_object_id == target_identity:
        facts = target_controller_library_facts(host, current)
    else:
        facts = before
    controller, types = facts
    return CommittedQuantityFollowup((FrozenMap({
        "op": REVEAL_OPERATION, "schema_version": 1, "player": controller,
        "spell_controller": caster, "target_ref": target_ref,
        "target_identity": target_identity, "target_types": types,
    }),))
