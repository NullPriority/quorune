"""Ordered controller-life-gain placement and a live named-counter release."""

from __future__ import annotations

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY, SOURCE_COUNTER_KIND_COUNT_CAPABILITY,
    ScalarAmountOrigin, ScalarEffectAmountSpec,
)

LIFE_GAIN_COUNTER_RESET_OPERATION = "clear_named_counters_then_untap_source"
LIFE_GAIN_COUNTER_RESET_CAPABILITY = "counter.resolution.fixed_life_gain_named_reset"
LIFE_GAIN_COUNTER_RESET_MECHANIC = "fixed-life-gain-counter-threshold-reset"
LIFE_GAIN_COUNTER_RESET_DEPENDENCIES = (
    LIFE_GAIN_COUNTER_RESET_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, SOURCE_COUNTER_KIND_COUNT_CAPABILITY,
    "trigger.event.normalized_life_gain", "trigger.placement.apnap",
    "counter.producer.fixed_effect", "counter.producer.fixed_event_trigger",
    "counter.placement.quantity_replacement", "counter.removal.all_named_source_effect",
    "permanent.untap.effect",
)


def life_gain_counter_reset_condition():
    return {"field": "player", "op": "eq", "value": "$source.controller"}


def life_gain_counter_reset_effects():
    count = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, counter_name="ribbon", schema_version=2)
    return (
        {"op": "place_counters", "card": "$source.zone_object", "counter": "ribbon",
         "amount": ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT).to_dict(), "source": "$source"},
        {"op": LIFE_GAIN_COUNTER_RESET_OPERATION, "schema_version": 1, "player": "$controller",
         "source": "$source.zone_object", "counter": "ribbon", "minimum": 3, "observed": count.to_dict()},
    )


def life_gain_counter_reset_node_capabilities(*, effects, target_schema, mechanic_ids=(), **_):
    if (target_schema is not None or LIFE_GAIN_COUNTER_RESET_MECHANIC not in mechanic_ids
            or tuple(map(FrozenMap, effects)) != tuple(map(FrozenMap, life_gain_counter_reset_effects()))):
        return ()
    followup = effects[1]
    if type(followup["schema_version"]) is not int or type(followup["minimum"]) is not int:
        return ()
    return LIFE_GAIN_COUNTER_RESET_DEPENDENCIES


def life_gain_counter_reset_program_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if (LIFE_GAIN_COUNTER_RESET_MECHANIC not in program.coverage
            and not any(effect.get("op") == LIFE_GAIN_COUNTER_RESET_OPERATION
                        for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    required = life_gain_counter_reset_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("life.gained", "battlefield")
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
            or "one_or_more_event_batch" in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(life_gain_counter_reset_condition())):
        raise ValueError("Named counter release requires its original controller life gain and ordered placement")
    return required


def resolved_counter_reset(effect, *, actor, stack_controller):
    expected = life_gain_counter_reset_effects()[1]
    if (
        not isinstance(effect, Mapping) or set(effect) != set(expected)
        or type(effect.get("schema_version")) is not int or type(effect.get("minimum")) is not int
        or effect.get("player") != actor or actor != stack_controller
        or type(effect.get("observed")) is not int or effect["observed"] < 0
        or (effect.get("source") is not None and (type(effect["source"]) is not str or not effect["source"]))
        or dict(effect) != {**expected, "player": actor, "source": effect.get("source"),
                            "observed": effect.get("observed")}
    ):
        raise ValueError("Named counter release lost its resolving source or live ribbon observation")
    return effect["observed"] >= effect["minimum"]
