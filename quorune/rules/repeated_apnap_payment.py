"""Closed opponent payment rounds from a spell or a loyalty result."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


FIELD = "repeated_apnap_payment"
MECHANIC = "announced-x-mixed-apnap-payment"
CAPABILITY = "choice.apnap.repeated_mixed_payment"
FIXED_DISCARD_VERSION = 2
FIXED_DISCARD_MECHANIC = "loyalty-fixed-discard-apnap-payment"
FIXED_DISCARD_CAPABILITY = "choice.apnap.repeated_loyalty_discard_payment"
REQUIREMENTS = (
    CAPABILITY, "choice.apnap.optional_object_payment_or_life_loss",
    "life.change.effect", "zone.change.destination_replacement",
)
FIXED_DISCARD_REQUIREMENTS = (
    FIXED_DISCARD_CAPABILITY, "choice.apnap.optional_object_payment_or_life_loss",
    "life.change.effect", "zone.change.destination_replacement",
)


def is_plain_loyalty_removal(cost):
    if not isinstance(cost, Mapping):
        return False
    delta = cost.get("loyalty_delta")
    if type(delta) is not int or delta >= 0 or cost.get("text") not in (str(delta), f"−{-delta}"):
        return False
    flags = ("tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source")
    quantities = ("life_payment", "energy_payment")
    expected = {
        "text": cost["text"], "mana": dict.fromkeys(("GENERIC", *"WUBRGC"), 0),
        "complex_symbols": [], "loyalty_delta": delta,
        "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
        **dict.fromkeys(flags, False), **dict.fromkeys(quantities, 0),
    }
    return (FrozenMap(cost) == FrozenMap(expected) and all(cost[key] is False for key in flags)
            and all(type(cost[key]) is int for key in quantities)
            and all(type(amount) is int for amount in cost["mana"].values()))


def is_plain_loyalty_removal_program(program):
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import activated_abilities_from_descriptors

    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or program.target_schema is not None or program.event_condition is not None
            or program.trigger_limit is not None or len(program.handlers) != 1
            or not is_plain_loyalty_removal(program.cost_schema)):
        return False
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1:
        return False
    ability, = abilities
    return (program.ability_id == f"ability:{ability.ability_id}" and ability.builtin_semantic_key == program.key
            and ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
            and not ability.activation_conditions and ability.target_schema is None
            and activated_ability_cost(ability) == thaw_value(program.cost_schema))


def payment_round_effect(loss):
    if type(loss) is not int or loss <= 0:
        raise ValueError("An APNAP payment round requires positive life loss")
    return {"op": "choose_cards_apnap", FIELD: 1, "declined_life_loss": loss}


def fixed_discard_round_effect(loss, rounds):
    if type(rounds) is not int or not 2 <= rounds <= 20:
        raise ValueError("A fixed discard payment requires two to twenty rounds")
    return {**payment_round_effect(loss), FIELD: FIXED_DISCARD_VERSION, "fixed_rounds": rounds}


def is_payment_round(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_cards_apnap" and FIELD in effect


def payment_round_descriptor(effect):
    version = effect.get(FIELD)
    if type(version) is int and version == 1:
        canonical = payment_round_effect(effect.get("declined_life_loss"))
    elif type(version) is int and version == FIXED_DISCARD_VERSION:
        canonical = fixed_discard_round_effect(effect.get("declined_life_loss"), effect.get("fixed_rounds"))
    else:
        raise ValueError("Repeated payment has an unknown descriptor version")
    if FrozenMap(effect) != FrozenMap(canonical):
        raise ValueError("Repeated payment must preserve its closed descriptor")
    return canonical


def payment_round_payload_dependencies(effect):
    return FIXED_DISCARD_REQUIREMENTS if payment_round_descriptor(effect)[FIELD] == FIXED_DISCARD_VERSION else REQUIREMENTS


def payment_round_node_capabilities(effects, target_schema, cost_schema, mechanics):
    known_mechanic = MECHANIC in mechanics or FIXED_DISCARD_MECHANIC in mechanics
    if not known_mechanic and not any(is_payment_round(effect) for effect in effects):
        return None
    if target_schema is not None or len(effects) != 1:
        return ()
    try:
        version = payment_round_descriptor(effects[0])[FIELD]
    except ValueError:
        return ()
    if version == FIXED_DISCARD_VERSION:
        return FIXED_DISCARD_REQUIREMENTS if FIXED_DISCARD_MECHANIC in mechanics and is_plain_loyalty_removal(cost_schema) else ()
    if MECHANIC in mechanics and cost_schema is None:
        return REQUIREMENTS
    return ()


def payment_round_program_dependencies(program):
    uses = any(is_payment_round(effect) for effect in nested_effects((program.effects, program.handlers)))
    if not uses and MECHANIC not in program.coverage and FIXED_DISCARD_MECHANIC not in program.coverage:
        return ()
    owned = payment_round_node_capabilities(program.effects, program.target_schema, program.cost_schema, program.coverage)
    if not owned or program.event_condition is not None:
        raise ValueError("Repeated payment requires one complete untargeted instruction")
    event_zone = (program.event, program.active_zone)
    if program.effects[0][FIELD] == FIXED_DISCARD_VERSION:
        if not is_plain_loyalty_removal_program(program):
            raise ValueError("Fixed discard rounds require their original battlefield loyalty ability")
    elif program.handlers or not program.ability_id.startswith("spell:") or event_zone != ("resolve", "stack"):
        raise ValueError("Announced payment rounds require their original spell resolution")
    return owned
