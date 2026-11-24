"""Bind one attack-trigger counter group and its own target's declaration restriction."""

from collections.abc import Mapping

from ..replacement.immutable import freeze_value, thaw_value
from .counter_capability_shapes import fixed_counter_placement_group_node_capabilities


ATTACK_COUNTER_UNBLOCKABLE_CAPABILITY = "trigger.combat.optional_attacker_counter_unblockable"
ATTACK_COUNTER_UNBLOCKABLE_MECHANIC = "fixed-attack-optional-target-counter-unblockable"
ATTACK_COUNTER_UNBLOCKABLE_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
_ATTACKER_TARGET = {
    "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
    "source_exclusion": True, "combat_state": "attacking",
}
_COUNTER_GROUP = {
    "op": "place_counters", "cards": ["$source.zone_object", "$target.0"],
    "counter": "+1/+1", "amount": 1, "source": "$source",
}
_UNBLOCKABLE = {
    "op": "grant_declaration_restriction_until_end_of_turn", "cards": "$targets",
    "restriction": "unblockable",
}


def attack_counter_unblockable_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (ATTACK_COUNTER_UNBLOCKABLE_MECHANIC not in mechanic_ids
            or not isinstance(target_schema, Mapping) or len(effects) != 2):
        return ()
    value = thaw_value(freeze_value(target_schema))
    expected = {**_ATTACKER_TARGET, "id": "target_0", "min": 0, "max": 1}
    if value != {"groups": [expected]}:
        return ()
    target, = value["groups"]
    if (type(target["min"]) is not int or type(target["max"]) is not int
            or target["source_exclusion"] is not True):
        return ()
    counters, restriction = thaw_value(freeze_value(effects))
    if counters != _COUNTER_GROUP or type(counters["amount"]) is not int or restriction != _UNBLOCKABLE:
        return ()

    from .node_capability_shapes import temporary_declaration_restriction_node_capabilities

    counter_owner = fixed_counter_placement_group_node_capabilities(
        effects=(counters,), target_schema=value, mechanic_ids=mechanic_ids,
    )
    restriction_owner = temporary_declaration_restriction_node_capabilities(
        effects=(restriction,), target_schema={**_ATTACKER_TARGET, "count": 1, "up_to": 1},
        mechanic_ids=mechanic_ids,
    )
    if not counter_owner or not restriction_owner:
        return ()
    return tuple(dict.fromkeys((
        ATTACK_COUNTER_UNBLOCKABLE_CAPABILITY, "trigger.event.normalized_public_action",
        "trigger.placement.apnap", "counter.producer.fixed_event_trigger",
        *counter_owner, *restriction_owner,
    )))


def attack_counter_unblockable_runtime_dependencies(program):
    if (ATTACK_COUNTER_UNBLOCKABLE_MECHANIC not in program.coverage
            and thaw_value(freeze_value(program.effects)) != [_COUNTER_GROUP, _UNBLOCKABLE]):
        return ()
    owners = attack_counter_unblockable_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not owners or program.event != "creature.attacks" or program.active_zone != "battlefield"
            or not program.ability_id.startswith("trigger:")
            or thaw_value(freeze_value(program.event_condition)) != ATTACK_COUNTER_UNBLOCKABLE_CONDITION
            or program.cost_schema is not None or getattr(program, "handlers", ())):
        raise ValueError("Attack counter and unblockable restriction require the original source attack trigger")
    return owners
