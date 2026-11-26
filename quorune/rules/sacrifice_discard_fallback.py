"""One public APNAP sacrifice round followed by private discards for players who cannot pay."""

from collections.abc import Mapping

from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import contains_nested_key


SACRIFICE_DISCARD_FALLBACK_FIELD = "creature_planeswalker_discard_fallback"
SACRIFICE_DISCARD_FALLBACK_CAPABILITY = "choice.affected_player.creature_planeswalker_discard_fallback"
SACRIFICE_DISCARD_FALLBACK_DEPENDENCIES = (
    SACRIFICE_DISCARD_FALLBACK_CAPABILITY, "choice.affected_player.fixed_sacrifice",
    "choice.affected_player.fixed_discard", "zone.change.destination_replacement",
)


def sacrifice_discard_fallback_effect(*, actor="$controller"):
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": "all", "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_any=("creature", "planeswalker")).to_dict(),
        "count": 1, "then": "sacrifice", "prompt": "Choose the required permanent(s) to sacrifice.",
        SACRIFICE_DISCARD_FALLBACK_FIELD: 1,
    }


def uses_sacrifice_discard_fallback(effect):
    return isinstance(effect, Mapping) and SACRIFICE_DISCARD_FALLBACK_FIELD in effect


def validate_sacrifice_discard_fallback(effect, *, actor="$controller"):
    if (not uses_sacrifice_discard_fallback(effect)
            or type(effect.get("count")) is not int or type(effect[SACRIFICE_DISCARD_FALLBACK_FIELD]) is not int
            or type(actor) is not str or not actor
            or FrozenMap(effect) != FrozenMap(sacrifice_discard_fallback_effect(actor=actor))):
        raise ValueError("Sacrifice fallback requires the exact all-player creature-or-planeswalker choice")


def sacrifice_discard_fallback_effects(*, actor, player_order, selected, active_seats):
    order = tuple(player_order)
    if (not order or len(order) != len(set(order)) or not isinstance(selected, Mapping)
            or set(selected) != set(order)
            or any(not isinstance(refs, (tuple, list)) or len(refs) > 1
                   or any(type(ref) is not str or not ref for ref in refs) for refs in selected.values())):
        raise ValueError("Sacrifice fallback requires a completed authoritative player round")
    missing = tuple(seat for seat in order if seat in active_seats and not selected[seat])
    if not missing:
        return ()
    return ({
        "op": "choose_cards_apnap", "actor": actor, "players": list(missing), "zone": "hand",
        "predicate": ObjectQuerySpec(zones=("hand",)).to_dict(),
        "count": 1, "then": "discard", "hidden": True, "prompt": "Choose a card to discard.",
    },)


def sacrifice_discard_fallback_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    try:
        validate_sacrifice_discard_fallback(effects[0])
    except ValueError:
        return ()
    return SACRIFICE_DISCARD_FALLBACK_DEPENDENCIES


def sacrifice_discard_fallback_program_dependencies(program):
    marked = contains_nested_key((program.effects, program.handlers, program.target_schema), SACRIFICE_DISCARD_FALLBACK_FIELD)
    if not marked and SACRIFICE_DISCARD_FALLBACK_CAPABILITY not in program.capability_dependencies:
        return ()
    required = sacrifice_discard_fallback_node_capabilities(program.effects, program.target_schema)
    if (not required or program.handlers or program.event_condition is not None or program.cost_schema is not None
            or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")):
        raise ValueError("Sacrifice and discard require the complete original source-entry trigger")
    return (*required, "trigger.event.normalized_zone_change", "trigger.placement.apnap")
