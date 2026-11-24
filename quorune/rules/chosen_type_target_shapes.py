from __future__ import annotations

"""A retained source creature type over two ordinary mandatory target slots."""

from collections.abc import Mapping

from ..entry_designations import CHOSEN_CREATURE_TYPE_TARGET_PREDICATE, ENTRY_DESIGNATION_CAPABILITY
from ..replacement.immutable import FrozenMap, thaw_value


CHOSEN_CREATURE_TYPE_TARGET_CAPABILITY = "target.public.source_chosen_creature_type"
_CONTROLLED_CREATURE = {
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1,
    "types_any": ["creature"], "controller_relation": "you",
}
_OWN_GRAVEYARD_CREATURE = {
    "zones": ["graveyard"], "categories": ["card"], "count": 1,
    "types_any": ["creature"], "owner_relation": "you",
}
_FINALITY_RETURN = {
    "op": "reanimate", "card": "$target.0", "controller": "$controller", "tapped": False,
    "schema_version": 3, "entry_counter": {"counter_name": "finality", "amount": 1}, "source": "$stack",
}
_REQUIRED = (
    CHOSEN_CREATURE_TYPE_TARGET_CAPABILITY, ENTRY_DESIGNATION_CAPABILITY, "target.revalidate_resolution",
)


def uses_chosen_creature_type_target(value) -> bool:
    if isinstance(value, Mapping):
        return (value.get("predicate") == CHOSEN_CREATURE_TYPE_TARGET_PREDICATE
                or any(uses_chosen_creature_type_target(child) for child in value.values()))
    if isinstance(value, (list, tuple)):
        return any(uses_chosen_creature_type_target(child) for child in value)
    return False


def _unqualified_target(effects, target_schema):
    if (not isinstance(target_schema, Mapping)
            or target_schema.get("predicate") != CHOSEN_CREATURE_TYPE_TARGET_PREDICATE
            or type(target_schema.get("count")) is not int or len(effects) != 1):
        raise ValueError("The chosen-type target requires one closed compulsory target group")
    schema = thaw_value(FrozenMap(target_schema))
    del schema["predicate"]
    effect = thaw_value(FrozenMap(effects[0]))
    from .temporary_self_copy import TEMPORARY_ARTIFACT_COPY_CAPABILITY, is_versioned_self_copy, temporary_self_copy_scope

    if is_versioned_self_copy(effect):
        temporary_self_copy_scope(effects, target_schema)
        return schema, "activate", (TEMPORARY_ARTIFACT_COPY_CAPABILITY,)
    if (schema == _CONTROLLED_CREATURE
            and set(effect) == {"op", "card", "counter", "amount", "source"}
            and effect["op"] == "place_counters" and effect["card"] == "$target.0"
            and effect["source"] == "$source" and type(effect["amount"]) is int and effect["amount"] == 1
            and isinstance(effect["counter"], str) and effect["counter"].isascii() and effect["counter"].isalpha()
            and effect["counter"].islower()):
        from ..keyword_counters import keyword_counter_mechanic

        if keyword_counter_mechanic(effect["counter"]) is None:
            return schema, "activate", ("counter.producer.fixed_effect",)
    if (schema == _OWN_GRAVEYARD_CREATURE and effect == _FINALITY_RETURN
            and effect["tapped"] is False and type(effect["schema_version"]) is int
            and type(effect["entry_counter"]["amount"]) is int):
        return schema, "step.begin", (
            "counter.producer.effect_entry", "counter.rule.finality",
            "zone.reanimate.entry_counters", "zone.reanimate.fixed_target",
        )
    raise ValueError("The chosen-type restriction requires its complete target scope and result")


def chosen_creature_type_target_node_capabilities(*, effects, target_schema, mechanics, cost_schema=None):
    from .capabilities import capability_dependencies_for_node

    try:
        base, _, _ = _unqualified_target(effects, target_schema)
    except (TypeError, ValueError):
        return ()
    owned = capability_dependencies_for_node(
        effects=effects, target_schema=base, mechanic_ids=mechanics, cost_schema=cost_schema,
    )
    return tuple(sorted({*owned, *_REQUIRED})) if owned else ()


def chosen_creature_type_target_runtime_dependencies(program):
    if not uses_chosen_creature_type_target(program.target_schema):
        return ()
    _, event, minimum = _unqualified_target(program.effects, program.target_schema)
    if program.event != event or program.active_zone != "battlefield":
        raise ValueError("The linked creature-type target requires its exact battlefield ability")
    return (*_REQUIRED, *minimum)



def is_closed_chosen_creature_type_target_program(program) -> bool:
    try:
        required = chosen_creature_type_target_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
