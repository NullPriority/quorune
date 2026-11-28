"""One controller draw quantity from the combat-damage source's counter kinds."""

from collections.abc import Mapping

from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_CAPABILITY,
    SCALAR_AMOUNT_KIND,
    SOURCE_COUNTER_KIND_COUNT_CAPABILITY,
    SOURCE_COUNTER_KIND_COUNT_VERSION,
    ScalarAmountOrigin,
    ScalarEffectAmountSpec,
    scalar_amount_specs,
)


def source_counter_kind_draw_effect():
    amount = ScalarEffectAmountSpec(
        ScalarAmountOrigin.SOURCE, schema_version=SOURCE_COUNTER_KIND_COUNT_VERSION,
    )
    return {"op": "draw", "player": "$controller", "count": amount.to_dict(), "private": True}


def uses_source_counter_kind_draw(effect):
    amount = effect.get("count")
    return (isinstance(amount, Mapping) and amount.get("kind") == SCALAR_AMOUNT_KIND
            and amount.get("schema_version") == SOURCE_COUNTER_KIND_COUNT_VERSION)


def source_combat_damage_to_player_condition_is_exact(condition):
    predicates = condition.get("all") if isinstance(condition, Mapping) and set(condition) == {"all"} else None
    expected = (
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
    )
    return isinstance(predicates, (list, tuple)) and tuple(predicates) == expected and predicates[1]["value"] is True


def source_counter_kind_draw_runtime_dependencies(program):
    values = (program.effects, getattr(program, "handlers", ()))
    if not any(spec.schema_version == SOURCE_COUNTER_KIND_COUNT_VERSION for spec in scalar_amount_specs(values)):
        return ()
    if ((program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None
            or tuple(program.effects) != (source_counter_kind_draw_effect(),)
            or not source_combat_damage_to_player_condition_is_exact(program.event_condition)
            or getattr(program, "handlers", ()) or getattr(program, "trigger_limit", None) is not None):
        raise ValueError("A distinct counter-kind draw requires its original combat source and player damage")
    return (SOURCE_COUNTER_KIND_COUNT_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "zone.draw.library_to_hand",
            "trigger.event.normalized_damage", "trigger.placement.apnap")
