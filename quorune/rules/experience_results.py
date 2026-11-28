"""Closed player experience results from ordinary turn and counter facts."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..turn_history import current_turn_history_events


EXPERIENCE_CAPABILITY = "counter.producer.discarding_player_experience_and_pump"
DISCARDERS_AMOUNT = "turn_discarding_player_count"
EXPERIENCE_AMOUNT = "controller_experience_counter_count"
EXPERIENCE_MECHANIC = "fixed-experience-discard-and-pump"
EXPERIENCE_ATTACK_TEMPLATE = "source-attack-optional-discard-and-discarder-experience-v1"
EXPERIENCE_PUMP_TEMPLATE = "source-controller-experience-power-toughness-menace-v1"


def experience_amount(kind):
    return {"kind": kind, "schema_version": 1}


def experience_attack_condition():
    return {"field": "card", "op": "eq", "value": "$source.ref"}


def experience_attack_effects():
    discard = {"op": "choose_cards_apnap", "actor": "$controller", "players": ["$controller"],
               "zone": "hand", "predicate": ObjectQuerySpec(zones=("hand",)).to_dict(),
               "count": 1, "then": "discard", "hidden": True, "prompt": "Choose the required card(s) to discard."}
    return (
        {"op": "offer_optional_effect", "player": "$controller", "effects": [discard]},
        {"op": "place_player_counters", "subjects": "controller", "source": "$source",
         "counter": "experience", "amount": experience_amount(DISCARDERS_AMOUNT), "zero_is_noop": True},
    )


def experience_pump_effect():
    amount = experience_amount(EXPERIENCE_AMOUNT)
    return {
        "op": "apply_source_characteristics_until_end_of_turn", "card": "$source.zone_object",
        "set_card_types": None, "set_subtypes": None, "set_colors": None,
        "base_power": None, "base_toughness": None,
        "power": amount, "toughness": amount, "keywords": ["Menace"],
    }


def experience_attack_dependencies():
    return (
        EXPERIENCE_CAPABILITY, "effect.choice.optional_fixed", "choice.affected_player.fixed_discard",
        "choice.affected_player.fixed_controller_discard", "counter.producer.fixed_player_effect",
        "trigger.event.normalized_self_attack", "trigger.effect.fixed_event", "trigger.placement.apnap",
        "condition.history.fixed_public_query",
    )


def experience_pump_dependencies():
    return (EXPERIENCE_CAPABILITY, "continuous.resolution.fixed_source_characteristics_until_end_of_turn",
            "combat.block.menace")


def uses_experience_value(value):
    return isinstance(value, Mapping) and value.get("kind") in {DISCARDERS_AMOUNT, EXPERIENCE_AMOUNT}


def _contains_experience_value(value):
    if isinstance(value, Mapping):
        return uses_experience_value(value) or any(_contains_experience_value(child) for child in value.values())
    return isinstance(value, (tuple, list)) and any(_contains_experience_value(child) for child in value)


def experience_program_dependencies(program):
    if not _contains_experience_value(program.effects):
        return ()
    carrier = (program.event, program.active_zone)
    if program.target_schema is not None:
        raise ValueError("Experience quantities have no target binding")
    if (carrier == ("creature.attacks", "battlefield") and not program.handlers
            and program.cost_schema is None
            and FrozenMap(program.event_condition or {}) == FrozenMap(experience_attack_condition())
            and tuple(map(FrozenMap, program.effects)) == tuple(map(FrozenMap, experience_attack_effects()))):
        return experience_attack_dependencies()
    if (carrier == ("activate", "battlefield") and program.event_condition is None
            and program.cost_schema is not None
            and tuple(map(FrozenMap, program.effects)) == (FrozenMap(experience_pump_effect()),)):
        return experience_pump_dependencies()
    raise ValueError("Experience quantities require their original attack or activated result")


def is_closed_experience_program(program):
    try:
        need = experience_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(need) and set(need) <= set(program.capability_dependencies)


def resolve_experience_amount(host, value, item):
    if type(value.get("schema_version")) is not int or value != experience_amount(value.get("kind")):
        raise ValueError("Experience amount requires its closed original descriptor")
    if value["kind"] == EXPERIENCE_AMOUNT:
        amount = host.state.players[item.controller].counters.get("experience", 0)
        if type(amount) is not int or amount < 0:
            raise ValueError("The resolving controller has invalid experience counters")
        return amount
    history = host.state.turn_history
    turn = host.state.turn_sequence
    if history is None or history.schema_version != 1 or history.turn_sequence != turn:
        raise ValueError("Experience requires the original current-turn discard journal")
    events = current_turn_history_events(history, turn_sequence=turn, kind="card_discarded")
    return len({row.actor for row in events if row.actor in host.active_seats})
