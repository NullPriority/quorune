"""One defender-scoped relative-power condition for a counter reflexive."""

from ..relative_power_target import (
    CREATURE_POWER_PREDICATE, RelativePowerSourceSnapshot, RelativePowerTargetCondition,
    current_effective_creature_power,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..reflexive_trigger_model import BOUND_REFLEXIVE_PROGRAM_VERSION, ReflexiveTriggerProgram


CAPABILITY = "trigger.reflexive.source_counter_defender_exile"


def attack_condition():
    return {"field": "card", "op": "eq", "value": "$source.ref"}


def _base_result():
    from ..compiler.conditional_exile_templates import ConditionalSourceExileTemplate

    schema = {"zones":["battlefield"], "categories":["permanent"], "types_any":["creature"], "count":1}
    return ConditionalSourceExileTemplate(FrozenMap(schema)).compiled()


def child_program():
    _, effects, schema, mechanics = _base_result()
    return ReflexiveTriggerProgram(effects,
        {**schema, "controller_seat":"$context.defending_player", "predicate":CREATURE_POWER_PREDICATE},
        mechanics, schema_version=BOUND_REFLEXIVE_PROGRAM_VERSION)


def child_capabilities(child):
    if child != child_program():
        return ()
    from .linked_exile_return_shapes import linked_exile_return_node_capabilities

    _, effects, schema, mechanics = _base_result()
    owned = linked_exile_return_node_capabilities(effects=effects, target_schema=schema, mechanic_ids=mechanics)
    return (CAPABILITY, *owned) if owned else ()


def bind_target_schema(host, parent, child, source):
    schema = thaw_value(child.target_schema)
    if child != child_program():
        return schema
    defender = parent.context.get("defending_player")
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != parent.context.get("source_logical_object_id")
            or type(defender) is not str or defender not in host.state.players
            or parent.context.get("event") != "creature.attacks"):
        raise ValueError("Defender-relative exile lost its original attack or source")
    known = RelativePowerSourceSnapshot(source.object_id, source.logical_object_id, source.ref,
                                         current_effective_creature_power(host, source))
    return {"groups":[{**schema, "controller_seat":defender,
                        "resolution_condition": RelativePowerTargetCondition(known).to_dict()}]}
