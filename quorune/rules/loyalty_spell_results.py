"""Exact ordinary loyalty activations using already-owned spell and damage effects."""

from collections.abc import Mapping

from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, DAMAGE_ALLOCATION_FIELD, DamageAllocationSpec
from ..compiler.impulse_access_templates import IMPULSE_ACCESS_CAPABILITY_ID, IMPULSE_ACCESS_MECHANIC_ID
from ..impulse_access_model import TOP_FREE_INSTANT_SORCERY_VERSION, top_free_instant_sorcery_effect
from ..next_spell_cost_model import (
    CAPABILITY as NEXT_COST_CAPABILITY, MECHANIC as NEXT_COST_MECHANIC, OPERATION as NEXT_COST_OPERATION,
    NEXT_TURN_SPELL_COST_VERSION, next_turn_spell_cost_effect,
)
from ..replacement.immutable import FrozenMap, immutable_fingerprint, thaw_value
from ..resolution_conditions import (
    RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_MECHANIC, is_other_blue_permanent_draw,
    other_blue_permanent_draw_effect,
)
from .captured_counter_keyword_grant import (
    CAPTURED_COUNTER_FLYING_CAPABILITY, CAPTURED_COUNTER_FLYING_DEPENDENCIES, CAPTURED_COUNTER_FLYING_MECHANICS,
    CAPTURED_COUNTER_FLYING_VERSION, CAPTURED_COUNTER_KEYWORD_OPERATION, captured_counter_keyword_instruction,
)
from .effect_shape_walkers import nested_effects

CAPABILITY = "activation.loyalty.fixed_spell_results"
MECHANIC = "fixed-loyalty-spell-results"
DAMAGE_MECHANICS = ("cr-120-damage", "cr-115-targets")
DRAW_MECHANIC = "cr-121-drawing-a-card"
_DAMAGING_RESULT = {
    "op": "damage_fixed_set", "source": "$source", "amount": 2, ANNOUNCED_DAMAGE_EFFECT_FIELD: True,
}
_DIVIDED_TARGET = {
    "groups": [{
        "categories": ["player", "permanent"], "predicate": "damageable",
        "zones": ["player", "battlefield"], "min": 1, "max": 2,
    }],
    DAMAGE_ALLOCATION_FIELD: DamageAllocationSpec(2).to_dict(),
}
_COST_DEFAULTS = {
    "mana": dict.fromkeys(("GENERIC", *"WUBRGC"), 0),
    "complex_symbols": [], "tap_source": False, "untap_source": False, "discard_source": False,
    "sacrifice_source": False, "exile_source": False, "life_payment": 0, "energy_payment": 0,
    "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
}


def loyalty_result_cost_is_closed(cost, delta):
    if not isinstance(cost, Mapping):
        return False
    text = cost.get("text")
    if delta == 0:
        allowed = ("0",)
    else:
        allowed = (str(delta), str(delta).replace("-", "−")) if delta < 0 else (f"+{delta}",)
    expected = thaw_value({**_COST_DEFAULTS, "text": text, "loyalty_delta": delta})
    return text in allowed and immutable_fingerprint(cost) == immutable_fingerprint(expected)


def uses_loyalty_spell_result(effect):
    version = effect.get("schema_version")
    return (
        (effect.get("op"), version) in (
            (NEXT_COST_OPERATION, NEXT_TURN_SPELL_COST_VERSION),
            ("fixed_impulse_access", TOP_FREE_INSTANT_SORCERY_VERSION),
            (CAPTURED_COUNTER_KEYWORD_OPERATION, CAPTURED_COUNTER_FLYING_VERSION),
        )
        or is_other_blue_permanent_draw(effect)
    )


def loyalty_spell_result_capabilities(*, effects, target_schema, active_zone, cost_schema, mechanics):
    if active_zone != "battlefield" or MECHANIC not in mechanics:
        return ()
    actual = tuple(immutable_fingerprint(effect) for effect in effects)
    delta = (cost_schema or {}).get("loyalty_delta")
    if type(delta) is not int or not loyalty_result_cost_is_closed(cost_schema, delta):
        return ()
    if delta == 0 and target_schema is None and set(CAPTURED_COUNTER_FLYING_MECHANICS).issubset(mechanics):
        exact = captured_counter_keyword_instruction(version=CAPTURED_COUNTER_FLYING_VERSION)
        if actual == (immutable_fingerprint(exact),):
            return (CAPABILITY, *CAPTURED_COUNTER_FLYING_DEPENDENCIES)
    if delta == 1 and target_schema is None and NEXT_COST_MECHANIC in mechanics:
        if actual == (immutable_fingerprint(next_turn_spell_cost_effect()),):
            return CAPABILITY, NEXT_COST_CAPABILITY
    if delta == -8 and target_schema is None and IMPULSE_ACCESS_MECHANIC_ID in mechanics:
        if actual == (immutable_fingerprint(top_free_instant_sorcery_effect()),):
            return CAPABILITY, IMPULSE_ACCESS_CAPABILITY_ID
    if (delta != -2 or target_schema is None
            or immutable_fingerprint(target_schema) != immutable_fingerprint(_DIVIDED_TARGET)
            or not {*DAMAGE_MECHANICS, DRAW_MECHANIC, RESOLUTION_CONDITION_MECHANIC}.issubset(mechanics)):
        return ()
    if actual != tuple(immutable_fingerprint(effect) for effect in (
        _DAMAGING_RESULT, other_blue_permanent_draw_effect(),
    )):
        return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities

    damage = fixed_damage_node_capabilities(effects=effects[:1], target_schema=target_schema, mechanic_ids=DAMAGE_MECHANICS)
    return (CAPABILITY, RESOLUTION_CONDITION_CAPABILITY, "zone.draw.library_to_hand", *damage) if damage else ()


def loyalty_spell_result_runtime_dependencies(program):
    if (not {CAPABILITY, CAPTURED_COUNTER_FLYING_CAPABILITY}.intersection(program.capability_dependencies)
            and MECHANIC not in program.coverage
            and not any(uses_loyalty_spell_result(effect) for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    required = loyalty_spell_result_capabilities(
        effects=program.effects, target_schema=program.target_schema, active_zone=program.active_zone,
        cost_schema=program.cost_schema, mechanics=program.coverage,
    )
    abilities = activated_abilities_from_descriptors(program.handlers)
    if (not required or program.event != "activate" or program.event_condition is not None
            or program.trigger_limit is not None or program.provenance.get("granted_only")
            or len(program.handlers) != 1 or len(abilities) != 1):
        raise ValueError("Fixed loyalty results require one exact ordinary battlefield activation")
    ability, = abilities
    if (ability.builtin_semantic_key != program.key or ability.zones != ("battlefield",)
            or ability.mana_ability or ability.sorcery_speed or ability.activation_conditions
            or ability.target_schema != (FrozenMap(program.target_schema) if program.target_schema is not None else None)
            or activated_ability_cost(ability) != thaw_value(program.cost_schema)):
        raise ValueError("Fixed loyalty results lost the ordinary activation catalog or exact price")
    return required


def is_closed_loyalty_spell_result_program(program):
    try:
        required = loyalty_spell_result_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
