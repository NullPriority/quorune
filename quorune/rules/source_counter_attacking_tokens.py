from __future__ import annotations

from collections.abc import Mapping

from ..attack_batch_model import ATTACK_BATCH_CONDITION, QUALIFIED_ATTACK_BATCH_CAPABILITY, attack_batch_conditions
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from .effect_shape_walkers import nested_effects


VERSION = 5
CAPABILITY = "token.creation.source_counter_attacking"
_SOURCE_COUNT = ScalarEffectAmountSpec(
    ScalarAmountOrigin.SOURCE, counter_name="+1/+1", schema_version=2,
)


def source_counter_attacking_token_amount():
    return _SOURCE_COUNT.to_dict()


def original_source_counter_effect():
    return {
        "op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1", "amount": 1,
        "source": "$source",
    }


def uses_source_counter_attacking_tokens(effect):
    return (
        isinstance(effect, Mapping) and effect.get("op") == "choose_fixed_attacking_token_destinations"
        and effect.get("token_entry_version") == VERSION
    )


def source_counter_attacking_token_closure(effect, *, resolved=False):
    from .attacking_token_results import attacking_token_result_closure

    if not uses_source_counter_attacking_tokens(effect) or type(effect["token_entry_version"]) is not int:
        raise ValueError("Attacking tokens require the exact source counter quantity version")
    amount = effect.get("quantity")
    if resolved:
        valid = type(amount) is int and amount >= 0
    else:
        valid = thaw_value(amount) == _SOURCE_COUNT.to_dict()
        if valid:
            valid = ScalarEffectAmountSpec.from_dict(amount) == _SOURCE_COUNT
    if not valid or effect.get("tapped") is not True:
        raise ValueError("Attacking tokens require the resolved original source counter count")
    ordinary = {**effect, "quantity": 1, "token_entry_version": 1}
    dependencies, mechanics = attacking_token_result_closure(ordinary)
    return tuple(sorted({CAPABILITY, SCALAR_AMOUNT_CAPABILITY, *dependencies})), mechanics


def source_counter_attacking_tokens_node_capabilities(effects, target_schema):
    if not any(uses_source_counter_attacking_tokens(effect) for effect in effects):
        return None
    if (target_schema is not None or len(effects) != 2 or thaw_value(effects[0]) != original_source_counter_effect()
            or type(effects[0].get("amount")) is not int):
        return ()
    try:
        required, _ = source_counter_attacking_token_closure(effects[1])
    except (TypeError, ValueError, KeyError):
        return ()
    return (*required, "counter.producer.fixed_effect", "counter.producer.fixed_event_trigger",
            "counter.placement.quantity_replacement")


def source_counter_attacking_tokens_program_dependencies(program):
    if not any(uses_source_counter_attacking_tokens(effect)
               for effect in nested_effects((program.effects, program.handlers, program.target_schema))):
        return ()
    required = source_counter_attacking_tokens_node_capabilities(program.effects, program.target_schema)
    specs = attack_batch_conditions(program.event_condition)
    if (not required or len(specs) != 1 or program.handlers or program.cost_schema is not None
            or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or "one_or_more_event_batch" not in program.coverage):
        raise ValueError("Source counter attacking tokens require one complete declared attack trigger")
    batch, = specs
    excluded = batch.query.excluded_subtypes
    condition = {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": ATTACK_BATCH_CONDITION, "op": "gte", "value": 1, "attack_batch": batch.to_dict()},
    ]}
    if (thaw_value(program.event_condition) != condition or batch.minimum != 1 or batch.grouping != "declaration" or batch.exclude_source
            or len(excluded) != 1 or batch.query != ObjectQuerySpec(
                zones=("battlefield",), types_all=("creature",), excluded_subtypes=excluded,
            )):
        raise ValueError("Source counter attacking tokens require the exact excluded-subtype attack scope")
    return (*required, QUALIFIED_ATTACK_BATCH_CAPABILITY)
