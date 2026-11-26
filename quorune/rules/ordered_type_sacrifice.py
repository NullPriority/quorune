"""One chosen permanent-type order produces three ordinary APNAP sacrifices."""

from collections.abc import Mapping
from itertools import permutations

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effect_operations


ORDERED_TYPE_SACRIFICE_OPERATION = "choose_sacrifice_type_order"
ORDERED_TYPE_SACRIFICE_CAPABILITY = "choice.resolution.permanent_type_sacrifice_order"
ORDERED_TYPE_SACRIFICE_RULES = ("101.4", "101.4b", "603.3", "608.2c", "608.2d", "608.2f", "609.3", "701.21a")
_ORDERED_TYPES = {
    ", ".join(order): order for order in permutations(("artifact", "creature", "land"))
}
SACRIFICE_ORDER_CHOICES = tuple(_ORDERED_TYPES)


def ordered_type_sacrifice_effect(*, actor="$controller"):
    return {"op": ORDERED_TYPE_SACRIFICE_OPERATION, "schema_version": 1, "player": actor}


def validate_ordered_type_sacrifice(effect, *, actor="$controller"):
    if (not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int
            or dict(effect) != ordered_type_sacrifice_effect(actor=actor)
            or type(actor) is not str or not actor or actor.startswith("$") and actor != "$controller"):
        raise ValueError("The fixed sacrifice order requires the original resolving controller and three types")


def ordered_sacrifice_stages(actor, chosen):
    if type(actor) is not str or not actor or actor.startswith("$") or type(chosen) is not str:
        raise ValueError("Choose one exact order for artifacts, creatures, and lands")
    stages = _ORDERED_TYPES.get(chosen)
    if stages is None:
        raise ValueError("Choose one exact order for artifacts, creatures, and lands")
    return tuple(FrozenMap({
        "op": "choose_cards_apnap", "actor": actor, "players": "all", "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=(kind,)).to_dict(),
        "count": count, "then": "sacrifice", "prompt": "Choose the required permanent(s) to sacrifice.",
    }) for count, kind in enumerate(stages, 1))


def ordered_type_sacrifice_node_capabilities(*, effects, target_schema, cost_schema=None):
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        validate_ordered_type_sacrifice(effects[0])
    except ValueError:
        return ()
    return (ORDERED_TYPE_SACRIFICE_CAPABILITY, "choice.affected_player.fixed_sacrifice",
            "zone.change.destination_replacement")


def ordered_type_sacrifice_program_dependencies(program):
    marked = ORDERED_TYPE_SACRIFICE_OPERATION in nested_effect_operations((program.effects, program.handlers, program.target_schema))
    if not marked and ORDERED_TYPE_SACRIFICE_CAPABILITY not in program.capability_dependencies:
        return ()
    required = ordered_type_sacrifice_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, cost_schema=program.cost_schema,
    )
    if (not required or program.handlers or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("The fixed sacrifice order requires its exact original non-targeted spell")
    return required


def is_closed_ordered_type_sacrifice_program(program):
    try:
        required = ordered_type_sacrifice_program_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
