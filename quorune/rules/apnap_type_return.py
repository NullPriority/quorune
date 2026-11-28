"""Closed APNAP type selection whose result is one owner-controlled return batch."""

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects

TYPE_RETURN_FIELD = "apnap_creature_type_return"
TYPE_RETURN_CAPABILITY = "zone.graveyard.return_apnap_chosen_type_union"
TYPE_RETURN_MECHANIC = "apnap-chosen-creature-type-return"


def type_return_effect():
    return {"op": "choose_creature_type", TYPE_RETURN_FIELD: 1}


def type_return_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (target_schema is None and TYPE_RETURN_MECHANIC in mechanic_ids and len(effects) == 1
            and type(effects[0].get(TYPE_RETURN_FIELD)) is int and FrozenMap(effects[0]) == FrozenMap(type_return_effect())):
        return (TYPE_RETURN_CAPABILITY, "trigger.placement.apnap", "zone.change.destination_replacement")
    return ()


def type_return_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers)) if TYPE_RETURN_FIELD in effect)
    if not marked and TYPE_RETURN_CAPABILITY not in program.capability_dependencies:
        return ()
    required = type_return_node_capabilities(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or len(marked) != 1 or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("The type union requires its original untargeted spell")
    return required


def is_closed_type_return_program(program):
    try:
        required = type_return_program_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_type_return(host, item, effect):
    if TYPE_RETURN_FIELD not in effect:
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_type_return_program(program) or item.kind not in {"spell", "spell_copy"}
            or item.context.get("currently_resolving") is not True or item.targets):
        raise ValueError("Public type selection requires its original resolving spell")
    if FrozenMap(effect) == FrozenMap(program.effects[0]):
        return {**effect, "player": host.apnap_order()[0]}
    if (set(effect) != set(type_return_effect()) | {"player", "_type_return_order", "_type_return_stack", "_type_return_choices"}
            or effect.get("_type_return_stack") != item.ref or type(effect[TYPE_RETURN_FIELD]) is not int
            or effect[TYPE_RETURN_FIELD] != 1):
        raise ValueError("The public type continuation changed its original stack authority")
    return effect
