"""Bind one existing counter kind to an original public activation target."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..util import stable_json
from .effect_shape_walkers import nested_effects


CHOSEN_COUNTER_PLACEMENT_OPERATION = "place_chosen_counter"
CHOSEN_COUNTER_PLACEMENT_CAPABILITY = "counter.producer.chosen_kind"
CHOSEN_COUNTER_PLACEMENT_FIELDS = frozenset({"op", "schema_version", "target", "source"})
_TARGET_SCHEMA = {"zones": ["player", "battlefield"], "categories": ["player", "permanent"], "count": 1}
_DEPENDENCIES = (
    CHOSEN_COUNTER_PLACEMENT_CAPABILITY,
    "counter.producer.fixed_effect",
    "counter.placement.quantity_replacement",
    "target.revalidate_resolution",
)
_ACTIVATION_COST = stable_json({
    "text": "{3}, {T}",
    "mana": {color: int(color == "GENERIC") * 3 for color in ("B", "C", "G", "GENERIC", "R", "U", "W")},
    "complex_symbols": [],
    "tap_source": True,
    "untap_source": False,
    "discard_source": False,
    "sacrifice_source": False,
    "exile_source": False,
    "life_payment": 0,
    "energy_payment": 0,
    "loyalty_delta": None,
    "source_counter_removal_cost": None,
    "choices": [],
    "uncompiled_costs": [],
})


def chosen_counter_placement_target_schema():
    return thaw_value(FrozenMap(_TARGET_SCHEMA))


def chosen_counter_placement_effect():
    return {"op": CHOSEN_COUNTER_PLACEMENT_OPERATION, "schema_version": 1,
            "target": "$target.0", "source": "$source"}


def decode_chosen_counter_placement(effect, *, bound=False):
    if (not isinstance(effect, Mapping) or set(effect) != CHOSEN_COUNTER_PLACEMENT_FIELDS
            or effect["op"] != CHOSEN_COUNTER_PLACEMENT_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1):
        raise ValueError("Chosen counter placement requires its closed instruction")
    if bound:
        for key in ("target", "source"):
            ref = effect[key]
            if ref is not None and (type(ref) is not str or not ref or ref.startswith("$")):
                raise ValueError("Counter placement requires resolved target and source references")
    elif dict(effect) != chosen_counter_placement_effect():
        raise ValueError("Chosen counter placement requires the original target and source")
    return effect["target"], effect["source"]


def chosen_counter_placement_price_is_closed(ability):
    from ..compiler.activated_costs import activated_ability_cost

    return (
        ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and not ability.activation_conditions and not ability.generic_reduction_per_legendary_creature
        and stable_json(activated_ability_cost(ability)) == _ACTIVATION_COST
    )


def chosen_counter_placement_node_capabilities(*, effects, target_schema, mechanic_ids, cost_schema=None):
    if (len(effects) != 1 or not isinstance(target_schema, Mapping)
            or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(_TARGET_SCHEMA)
            or not {"cr-115-targets", "cr-122-counters"}.issubset(mechanic_ids)):
        return ()
    try:
        decode_chosen_counter_placement(effects[0])
    except (TypeError, ValueError):
        return ()
    return _DEPENDENCIES


def chosen_counter_placement_program_dependencies(program):
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if effect.get("op") == CHOSEN_COUNTER_PLACEMENT_OPERATION)
    if not marked and CHOSEN_COUNTER_PLACEMENT_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(marked) != 1 or effects != marked or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Chosen placement requires its complete original battlefield activation")
    required = chosen_counter_placement_node_capabilities(
        effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required or stable_json(thaw_value(program.cost_schema)) != _ACTIVATION_COST:
        raise ValueError("Chosen placement requires one public permanent or player and its printed tap price")
    try:
        abilities = activated_abilities_from_descriptors(program.handlers)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("The chosen placement activation catalog is malformed") from exc
    if len(abilities) != 1:
        raise ValueError("The chosen placement requires one activation descriptor")
    ability = abilities[0]
    if (program.ability_id != f"ability:{ability.ability_id}" or ability.builtin_semantic_key != program.key
            or ability.target_schema != FrozenMap(_TARGET_SCHEMA)
            or not chosen_counter_placement_price_is_closed(ability)):
        raise ValueError("The chosen placement activation catalog changed its exact target or price")
    return required


def is_closed_chosen_counter_placement_program(program):
    try:
        required = chosen_counter_placement_program_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
