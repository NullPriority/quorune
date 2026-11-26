"""Original attack and resolution-time source counters for variable Annihilator."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY, ScalarAmountOrigin, ScalarEffectAmountSpec
from .effect_shape_walkers import nested_effects


COUNTER_SCALED_ANNIHILATOR_CAPABILITY = "trigger.keyword.annihilator.source_plus_counters"
COUNTER_SCALED_ANNIHILATOR_MECHANIC = "annihilator-source-plus-counters"
COUNTER_SCALED_ANNIHILATOR_FIELD = "annihilator_source_plus_counters"
ANNIHILATOR_ATTACK_CONDITION = FrozenMap({"field": "card", "op": "eq", "value": "$source.ref"})
_ANNIHILATOR_AMOUNT = ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, counter_name="+1/+1", schema_version=2)


def annihilator_sacrifice_effect(count):
    return {
        "op": "choose_cards_apnap", "actor": "$controller", "players": ["$context.defending_player"],
        "zone": "battlefield", "predicate": ObjectQuerySpec(zones=("battlefield",)).to_dict(),
        "count": count, "then": "sacrifice", "prompt": "Choose the required permanent(s) to sacrifice.",
    }


def counter_scaled_annihilator_effect():
    return {**annihilator_sacrifice_effect(_ANNIHILATOR_AMOUNT.to_dict()), COUNTER_SCALED_ANNIHILATOR_FIELD: 1}


def uses_counter_scaled_annihilator(effect):
    return isinstance(effect, Mapping) and COUNTER_SCALED_ANNIHILATOR_FIELD in effect


def _validate(effect):
    if (not uses_counter_scaled_annihilator(effect)
            or type(effect[COUNTER_SCALED_ANNIHILATOR_FIELD]) is not int
            or FrozenMap(effect) != FrozenMap(counter_scaled_annihilator_effect())
            or ScalarEffectAmountSpec.from_dict(effect["count"]) != _ANNIHILATOR_AMOUNT):
        raise ValueError("Variable Annihilator requires its exact source counter and original defender")


def counter_scaled_annihilator_dependencies():
    return (
        COUNTER_SCALED_ANNIHILATOR_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, "trigger.keyword.annihilator.fixed",
        "trigger.event.normalized_self_attack", "trigger.placement.apnap",
    )


def counter_scaled_annihilator_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((effects, program.handlers))
                   if uses_counter_scaled_annihilator(effect))
    if not marked and COUNTER_SCALED_ANNIHILATOR_CAPABILITY not in program.capability_dependencies:
        return ()
    if ((program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or FrozenMap(program.event_condition or {}) != ANNIHILATOR_ATTACK_CONDITION
            or len(marked) != 1 or effects != marked or program.handlers
            or program.target_schema is not None or program.cost_schema is not None
            or not {CURRENT_ABILITY_FRAGMENT_COVERAGE, COUNTER_SCALED_ANNIHILATOR_MECHANIC}.issubset(program.coverage)):
        raise ValueError("Variable Annihilator requires one current intrinsic self-attack trigger")
    _validate(effects[0])
    return counter_scaled_annihilator_dependencies()


def bind_counter_scaled_annihilator(host, item, effect):
    if not uses_counter_scaled_annihilator(effect):
        return effect
    _validate(effect)
    defender = item.context.get("defending_player")
    if (item.kind != "triggered_ability" or item.context.get("event") != "creature.attacks"
            or type(defender) is not str or defender not in host.state.players or defender == item.controller):
        raise ValueError("Variable Annihilator lost the original defending player")
    if not host.state.players[defender].in_game:
        return None
    from ..scalar_effect_amounts import resolve_scalar_effect_amount

    count = resolve_scalar_effect_amount(host, effect["count"], item, use_phased_source_lki=True)
    return annihilator_sacrifice_effect(count) if count else None
