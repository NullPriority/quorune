"""The recorded mutation count on the original tap ability's source."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY, SCALAR_AMOUNT_KIND, SCALAR_AMOUNT_MECHANIC,
    SOURCE_MUTATION_COUNT_CAPABILITY, SOURCE_MUTATION_COUNT_VERSION,
    ScalarAmountOrigin, ScalarEffectAmountSpec,
)

TEMPLATE = "fixed-source-mutation-count-damage-v1"
_TAP_COST = {
    "text": "{T}", "mana": {color: 0 for color in ("B", "C", "G", "GENERIC", "R", "U", "W")},
    "complex_symbols": [], "tap_source": True, "untap_source": False,
    "discard_source": False, "sacrifice_source": False, "exile_source": False,
    "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
    "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def source_mutation_damage_target():
    return {"zones": ["player", "battlefield"], "categories": ["player", "permanent"],
            "predicate": "damageable", "count": 1}


def source_mutation_damage_effect():
    amount = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, schema_version=SOURCE_MUTATION_COUNT_VERSION)
    return {"op": "damage", "source": "$source", "target": "$target.0", "amount": amount.to_dict()}


def source_mutation_damage_template():
    return TEMPLATE, (source_mutation_damage_effect(),), source_mutation_damage_target(), (
        SCALAR_AMOUNT_MECHANIC, "cr-120-damage", "cr-115-targets",
    )


def _marks_mutation_amount(values):
    def marked(value):
        if isinstance(value, Mapping):
            if value.get("kind") == SCALAR_AMOUNT_KIND and value.get("schema_version") == SOURCE_MUTATION_COUNT_VERSION:
                return True
            return any(marked(child) for child in value.values())
        return isinstance(value, (tuple, list)) and any(marked(child) for child in value)

    return marked(values)


def source_mutation_damage_runtime_dependencies(program):
    values = (program.effects, program.handlers, program.target_schema, program.cost_schema)
    if not (_marks_mutation_amount(values)
            or SOURCE_MUTATION_COUNT_CAPABILITY in program.capability_dependencies):
        return ()
    cost = program.cost_schema or {}
    effects = tuple(program.effects)
    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or program.trigger_limit is not None
            or FrozenMap(cost) != FrozenMap(_TAP_COST)
            or any(type(cost[key]) is not type(expected) for key, expected in _TAP_COST.items()
                   if type(expected) in (bool, int))
            or any(type(number) is not int for number in cost["mana"].values())
            or FrozenMap(program.target_schema or {}) != FrozenMap(source_mutation_damage_target())
            or len(effects) != 1 or FrozenMap(effects[0]) != FrozenMap(source_mutation_damage_effect())
            or ScalarEffectAmountSpec.from_dict(effects[0]["amount"]).schema_version != SOURCE_MUTATION_COUNT_VERSION
            or any(handler.get("handler_id") != "activation.catalog.pinned.v1" for handler in program.handlers)):
        raise ValueError("Source mutation count requires its exact battlefield tap ability and ordinary damage target")
    return (SCALAR_AMOUNT_CAPABILITY, SOURCE_MUTATION_COUNT_CAPABILITY)
