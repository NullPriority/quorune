"""A full source trigger resolution can produce a separately placed optional fight."""

from ..errors import GameRuleError
from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_LKI_CONTEXT
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_CAPABILITY, REFLEXIVE_PAYMENT_MECHANIC,
    THIRD_SOURCE_TRIGGER_FIELD, ReflexiveTriggerProgram,
)
from ..replacement.immutable import FrozenMap, freeze_value, thaw_value
from .creature_power_damage_capability_shapes import fixed_creature_power_damage_node_capabilities
from .effect_shape_walkers import contains_nested_key


THIRD_SOURCE_TRIGGER_CAPABILITY = "trigger.reflexive.third_source_resolution"
SOURCE_COUNTER = {
    "op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1",
    "amount": 1, "source": "$source",
}
THIRD_SOURCE_TRIGGER_CONDITION = {"all": [
    {"field": "controller", "op": "eq", "value": "$source.controller"},
    {"field": "card", "op": "ne", "value": "$source.ref"},
]}
_LIVE_SOURCE_FIGHT = {
    "op": "creature_power_damage", "kind": "fight", "source": "$source.zone_object",
    "target": "$target.0", "target_must_be_creature": True, "source_lki": None,
}
_OTHER_CREATURE = {"groups": [{
    "id": "opponent", "zones": ["battlefield"], "categories": ["permanent"],
    "types_any": ["creature"], "controller_relation": "opponent", "min": 0, "max": 1,
}]}


def third_source_trigger_child(value):
    child = ReflexiveTriggerProgram.from_dict(value)
    target = thaw_value(child.target_schema)
    if (child.schema_version != BOUND_REFLEXIVE_PROGRAM_VERSION or target != _OTHER_CREATURE
            or len(child.effects) != 1 or thaw_value(child.effects[0]) != _LIVE_SOURCE_FIGHT
            or child.effects[0]["target_must_be_creature"] is not True):
        raise ValueError("The third resolution requires one live source fight with an optional opponent creature")
    group, = target["groups"]
    if type(group["min"]) is not int or type(group["max"]) is not int:
        raise ValueError("The reflexive fight target has malformed bounds")
    # Fight already requires the original live creature. The zone-object
    # reference binds that incarnation without a separate damage snapshot.
    comparison = {**_LIVE_SOURCE_FIGHT, "source": "$source",
                  "source_lki": "$context." + CREATURE_POWER_DAMAGE_LKI_CONTEXT}
    owners = fixed_creature_power_damage_node_capabilities(
        effects=(comparison,), target_schema=target, mechanic_ids=child.mechanic_ids,
    )
    if not owners:
        raise ValueError("The reflexive fight lacks the canonical fight authority")
    return child, owners


def third_source_trigger_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or target_schema is not None
            or not {"cr-122-counters", REFLEXIVE_PAYMENT_MECHANIC} <= set(mechanic_ids)):
        return ()
    effect = effects[0]
    prefix = {key: val for key, val in effect.items() if key != THIRD_SOURCE_TRIGGER_FIELD}
    if prefix != SOURCE_COUNTER or type(prefix["amount"]) is not int:
        return ()
    try:
        child, owners = third_source_trigger_child(effect[THIRD_SOURCE_TRIGGER_FIELD])
    except (TypeError, ValueError, KeyError):
        return ()
    if not set(child.mechanic_ids) <= set(mechanic_ids):
        return ()
    return tuple(dict.fromkeys((
        THIRD_SOURCE_TRIGGER_CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY,
        "condition.history.fixed_public_query", "counter.producer.fixed_effect",
        "counter.producer.fixed_event_trigger", "trigger.event.normalized_zone_change",
        "trigger.placement.apnap", *owners,
    )))


def third_source_trigger_runtime_dependencies(program):
    if not contains_nested_key((*program.effects, *getattr(program, "handlers", ())), THIRD_SOURCE_TRIGGER_FIELD):
        return ()
    owners = third_source_trigger_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not owners or program.event != "creature.dies" or program.active_zone != "battlefield"
            or not program.ability_id.startswith("trigger:")
            or thaw_value(freeze_value(program.event_condition)) != THIRD_SOURCE_TRIGGER_CONDITION
            or program.cost_schema is not None or getattr(program, "handlers", ())):
        raise ValueError("A third-resolution fight requires the exact source death trigger and counter")
    return owners


def _admitted_third_source_trigger(host, item, program):
    if (item.kind != "triggered_ability" or item.targets or item.controller not in host.active_seats
            or not host.semantic_program_is_current_trusted(program)):
        raise GameRuleError("A reflexive fight requires its original trusted resolving trigger")
    try:
        owners = third_source_trigger_runtime_dependencies(program)
        if not owners or not set(owners) <= set(program.capability_dependencies):
            raise ValueError("The creating trigger has incomplete third-resolution authority")
        return third_source_trigger_child(program.effects[0][THIRD_SOURCE_TRIGGER_FIELD])[0]
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc


def ordinary_third_source_counter(host, item, effects):
    if not contains_nested_key(effects, THIRD_SOURCE_TRIGGER_FIELD):
        return None
    program = host.semantics.get(item.semantic_key)
    if (program is None or len(effects) != 1 or len(program.effects) != 1
            or FrozenMap(effects[0]) != FrozenMap(program.effects[0])):
        raise GameRuleError("The third-resolution fight changed its original counter instruction")
    _admitted_third_source_trigger(host, item, program)
    return [dict(SOURCE_COUNTER)]


def complete_third_source_trigger(host, item, *, record_ordinal):
    if item.kind != "triggered_ability" or item.semantic_key is None:
        return
    program = host.semantics.get(item.semantic_key)
    if program is None or not any(THIRD_SOURCE_TRIGGER_FIELD in effect for effect in program.effects):
        return
    if item.context.get("currently_resolving") is not True:
        raise GameRuleError("Only a completing resolution can place its reflexive fight")
    child = _admitted_third_source_trigger(host, item, program)
    if record_ordinal(host, item) == 3:
        from .reflexive_triggers import _place_reflexive_trigger

        _place_reflexive_trigger(host, item, child)
