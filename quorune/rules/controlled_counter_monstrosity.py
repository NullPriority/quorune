"""One Monstrosity result whose X counts the ability controller's creature counters."""

from collections.abc import Mapping

from ..characteristic_fragments import (
    CharacteristicQuantityReduction, CharacteristicQuantityScope, CharacteristicQuantitySpec,
)
from ..object_predicate import ObjectQuerySpec
from ..query_effect_amount_model import PublicQueryAmountSpec, public_query_amount_runtime_dependencies
from ..replacement.immutable import thaw_value


CONTROLLED_COUNTER_MONSTROSITY_CAPABILITY = "keyword_action.monstrosity.controlled_creature_counters"
CONTROLLED_COUNTER_MONSTROSITY_MECHANIC = "controlled-creature-counter-monstrosity"
_BINDING = "quantity:x:controlled-creature-counter-monstrosity"
_COST = {
    "text": "{3}{G}{G}",
    "mana": {"B": 0, "C": 0, "G": 2, "GENERIC": 3, "R": 0, "U": 0, "W": 0},
    "complex_symbols": [], "tap_source": False, "untap_source": False, "discard_source": False,
    "sacrifice_source": False, "exile_source": False, "life_payment": 0, "energy_payment": 0,
    "loyalty_delta": None, "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def controlled_counter_monstrosity_effect():
    quantity = CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        schema_version=2,
        reduction=CharacteristicQuantityReduction.TOTAL_COUNTERS,
    )
    amount = PublicQueryAmountSpec(quantity, schema_version=2, binding_id=_BINDING)
    return {"op": "fixed_self_counter_keyword_action", "action": "monstrosity",
            "amount": amount.to_dict(), "source": "$source"}


def controlled_counter_monstrosity_cost_is_closed(cost_schema):
    return (cost_schema == _COST
            and all(type(cost_schema[field]) is type(value) for field, value in _COST.items()
                    if isinstance(value, (bool, int)))
            and all(type(amount) is int for amount in cost_schema["mana"].values()))


def uses_controlled_counter_monstrosity(effect):
    return (effect.get("op") == "fixed_self_counter_keyword_action" and effect.get("action") == "monstrosity"
            and isinstance(effect.get("amount"), Mapping))


def controlled_counter_monstrosity_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None or not uses_controlled_counter_monstrosity(effects[0]):
        return ()
    actual = thaw_value(effects[0])
    binding = actual["amount"].get("binding_id")
    if type(binding) is not str or not (binding == _BINDING or binding.endswith(":" + _BINDING)):
        return ()
    actual["amount"]["binding_id"] = _BINDING
    if actual != controlled_counter_monstrosity_effect():
        return ()
    return (
        CONTROLLED_COUNTER_MONSTROSITY_CAPABILITY, "keyword_action.monstrosity.fixed",
        "counter.placement.quantity_replacement", "quantity_expression.public_counter_total",
        *public_query_amount_runtime_dependencies(effects[0]["amount"]),
    )


def controlled_counter_monstrosity_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    if (CONTROLLED_COUNTER_MONSTROSITY_MECHANIC not in program.coverage
            and not any(uses_controlled_counter_monstrosity(effect)
                        for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    dependencies = controlled_counter_monstrosity_node_capabilities(program.effects, program.target_schema)
    if (not dependencies or (program.event, program.active_zone) != ("activate", "battlefield")
            or not controlled_counter_monstrosity_cost_is_closed(program.cost_schema)
            or len(program.handlers) > 1
            or any(handler.get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID for handler in program.handlers)):
        raise ValueError("Controlled-creature counter Monstrosity requires its exact fixed-cost activation")
    return dependencies
