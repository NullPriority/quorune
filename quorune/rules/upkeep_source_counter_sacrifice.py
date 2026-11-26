"""The sealed upkeep participant sacrifices for the original source's soot counters."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from .effect_shape_walkers import nested_effects


UPKEEP_SOURCE_COUNTER_SACRIFICE_FIELD = "upkeep_source_counter_sacrifice"
UPKEEP_SOURCE_COUNTER_SACRIFICE_CAPABILITY = "choice.affected_player.upkeep_source_counters"
_UPKEEP_CONDITION = {"all": [{"field": "step", "op": "eq", "value": "upkeep"}]}
_SOOT_AMOUNT = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, counter_name="soot", schema_version=2)


def upkeep_source_counter_sacrifice_effect():
    return {
        "op": "choose_cards_apnap", "actor": "$controller", "players": ["$context.player"],
        "zone": "battlefield", "predicate": ObjectQuerySpec(zones=("battlefield",)).to_dict(),
        "count": _SOOT_AMOUNT.to_dict(), "then": "sacrifice",
        "prompt": "Choose the required permanent(s) to sacrifice.",
        UPKEEP_SOURCE_COUNTER_SACRIFICE_FIELD: 1,
    }


def uses_upkeep_source_counter_sacrifice(effect):
    return isinstance(effect, Mapping) and UPKEEP_SOURCE_COUNTER_SACRIFICE_FIELD in effect


def _validate(effect):
    if (not uses_upkeep_source_counter_sacrifice(effect)
            or type(effect[UPKEEP_SOURCE_COUNTER_SACRIFICE_FIELD]) is not int
            or thaw_value(effect) != upkeep_source_counter_sacrifice_effect()
            or ScalarEffectAmountSpec.from_dict(effect["count"]) != _SOOT_AMOUNT
            or ObjectQuerySpec.from_dict(effect["predicate"]) != ObjectQuerySpec(zones=("battlefield",))):
        raise ValueError("This upkeep sacrifice requires its sealed player and exact source soot count")


def upkeep_source_counter_sacrifice_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    try:
        _validate(effects[0])
    except ValueError:
        return ()
    from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
    from .scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY

    normalized = {key: value for key, value in effects[0].items()
                  if key != UPKEEP_SOURCE_COUNTER_SACRIFICE_FIELD}
    normalized.update(count=1, players=["$controller"])
    owned = fixed_affected_player_sacrifice_node_capabilities(
        effects=(normalized,), target_schema=None,
        mechanic_ids=("fixed-affected-player-sacrifice", "sacrifice"),
    )
    return (UPKEEP_SOURCE_COUNTER_SACRIFICE_CAPABILITY, SCALAR_AMOUNT_CAPABILITY,
            SCHEDULED_PLAYER_CAPABILITY, *owned) if owned else ()


def upkeep_source_counter_sacrifice_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_upkeep_source_counter_sacrifice(effect))
    if not marked and UPKEEP_SOURCE_COUNTER_SACRIFICE_CAPABILITY not in program.capability_dependencies:
        return ()
    from .scheduled_player_shapes import SCHEDULED_PLAYER_DEPARTURE_COVERAGE

    owned = upkeep_source_counter_sacrifice_node_capabilities(program.effects, program.target_schema)
    if (not owned or tuple(program.effects) != marked or program.event != "step.begin"
            or program.active_zone != "battlefield" or thaw_value(program.event_condition) != _UPKEEP_CONDITION
            or SCHEDULED_PLAYER_DEPARTURE_COVERAGE not in program.coverage
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Source-counter sacrifice requires the exact original battlefield upkeep trigger")
    return owned


def bind_upkeep_source_counter_sacrifice(host, item, effect):
    if not uses_upkeep_source_counter_sacrifice(effect):
        return effect
    _validate(effect)
    seat = item.context.get("player")
    if (item.kind != "triggered_ability" or item.context.get("event") != "step.begin"
            or item.context.get("step") != "upkeep" or type(seat) is not str or seat not in host.state.players):
        raise ValueError("Source-counter sacrifice lost the original upkeep participant")
    if not host.state.players[seat].in_game:
        return None
    from ..scalar_effect_amounts import resolve_scalar_effect_amount

    amount = resolve_scalar_effect_amount(host, effect["count"], item, use_phased_source_lki=True)
    if not amount:
        return None
    return {key: amount if key == "count" else value for key, value in effect.items()
            if key != UPKEEP_SOURCE_COUNTER_SACRIFICE_FIELD}
