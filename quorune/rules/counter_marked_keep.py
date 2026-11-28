"""Closed APNAP retention and sacrifice around a named counter duration."""

from collections.abc import Mapping

from ..counter_bound_attack_model import COUNTER_BOUND_ATTACK_CAPABILITY, COUNTER_BOUND_ATTACK_OPERATION
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap


KEEP_COUNTER_FIELD = "keep_creature_with_counter"
KEEP_COUNTER_COHORT = "counter_marked_keep_incarnations"
KEEP_COUNTER_CAPABILITY = "choice.affected_player.keep_creature_with_counter"
COUNTER_NAME = "vow"


def keep_counter_effect(actor="$controller"):
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": "all", "zone": "battlefield",
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
        "count": 1, "then": "sacrifice", KEEP_COUNTER_FIELD: 1,
        "prompt": "Choose a creature you control to receive a vow counter and keep.",
    }


def require_counter_keep_effect(effect, *, actor="$controller"):
    if (not isinstance(effect, Mapping) or type(effect.get(KEEP_COUNTER_FIELD)) is not int
            or type(effect.get("count")) is not int or FrozenMap(effect) != FrozenMap(keep_counter_effect(actor))):
        raise ValueError("Counter-marked retention requires one creature from each player")


def keep_counter_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or target_schema is not None
            or not {"fixed-affected-player-sacrifice", "sacrifice"} <= set(mechanic_ids)):
        return ()
    try:
        require_counter_keep_effect(effects[0])
    except (TypeError, ValueError):
        return ()
    return (KEEP_COUNTER_CAPABILITY, COUNTER_BOUND_ATTACK_CAPABILITY, "counter.producer.fixed_effect",
            "choice.affected_player.fixed_sacrifice", "zone.graveyard.ordered_simultaneous_arrival",
            "zone.change.destination_replacement")


def keep_counter_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    if (not contains_nested_key((*program.effects, *program.handlers), KEEP_COUNTER_FIELD)
            and KEEP_COUNTER_CAPABILITY not in program.capability_dependencies):
        return ()
    caps = keep_counter_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not caps or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or program.event_condition is not None
            or program.handlers or program.cost_schema is not None or program.requires_arbiter):
        raise ValueError("Counter-marked retention requires its closed untargeted spell")
    return caps


def keep_counter_followups(kept_refs, identities, *, actor, source):
    """Post-choice commands pass the sealed refs; the existing owners commit them."""
    if not kept_refs:
        return (), ()
    place = {
        "op": "place_counters", "counter": COUNTER_NAME, "amount": 1, "source": source,
        "reason": "Mark the creatures kept by each player.",
    }
    if len(kept_refs) == 1:
        place["card"] = kept_refs[0]
    else:
        place["cards"] = list(kept_refs)
        place[KEEP_COUNTER_FIELD] = 1
    restrict = {
        "op": COUNTER_BOUND_ATTACK_OPERATION, "schema_version": 1,
        "protected_player": actor, "counter": COUNTER_NAME,
        "subjects": [{"ref": ref, "logical_object_id": identities[ref]} for ref in kept_refs],
    }
    return (place,), (restrict,)



def is_closed_keep_counter_program(program):
    try:
        required = keep_counter_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required and set(required) <= set(program.capability_dependencies))
