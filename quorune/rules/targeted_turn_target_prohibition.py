"""Closed turn-long restriction tied to a resolving caster and one incarnation."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..resolution_conditions import (
    CAST_FACT_CONDITION_CAPABILITY, CAST_FACT_CONDITION_MECHANIC,
    KickedCastCondition, RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_MECHANIC,
    RESOLUTION_CONDITION_OPERATION, SAME_TARGET_SELF_REPLACEMENT_CAPABILITY,
    validate_resolution_condition_instruction,
)
from ..turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_MECHANIC,
    TURN_CREATURE_TARGET_PROHIBITION_OPERATION,
)
from .effect_shape_walkers import nested_effects

CAPABILITY = "target.prohibition.targeted_creature_from_casters_opponents_for_turn"
MECHANIC = TURN_CREATURE_TARGET_PROHIBITION_MECHANIC
WINDOW = "turn_targeted_creature_caster_opponent_prohibitions"
VERSION = 2
BONUS_MECHANICS = ("cr-611-continuous-effects", "cr-115-targets")


def targeted_turn_prohibition(*, player="$controller", target="$target.0"):
    return {"op": TURN_CREATURE_TARGET_PROHIBITION_OPERATION, "schema_version": VERSION,
            "player": player, "target": target}


def marks_targeted_turn_prohibition(effect):
    return (effect.get("op") == TURN_CREATURE_TARGET_PROHIBITION_OPERATION
            and (effect.get("schema_version") == VERSION or "target" in effect))


def targeted_turn_prohibition_target():
    return {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "count": 1}


def targeted_turn_prohibition_spell():
    grant = targeted_turn_prohibition()
    bonus = {"op": "modify_stats_until_end_of_turn", "card": "$target.0", "power": 4, "toughness": 4}
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": 5, "player": "$controller",
        "condition": KickedCastCondition().to_dict(), "cast_fact": "$context.kicked",
        "effects": [grant, bonus], "mechanic_ids": [MECHANIC, *BONUS_MECHANICS],
        "otherwise_effects": [grant], "otherwise_mechanic_ids": [MECHANIC],
        "prefix_mechanic_ids": [],
    }
    return (wrapper,), targeted_turn_prohibition_target(), (
        RESOLUTION_CONDITION_MECHANIC, CAST_FACT_CONDITION_MECHANIC, MECHANIC, *BONUS_MECHANICS,
    )


def uses_targeted_turn_prohibition(values):
    return any(marks_targeted_turn_prohibition(effect) for effect in nested_effects(values))


def targeted_turn_prohibition_node_capabilities(effects, target_schema, mechanic_ids):
    from .node_capability_shapes import fixed_target_characteristics_node_capabilities

    expected, schema, mechanics = targeted_turn_prohibition_spell()
    if (tuple(FrozenMap(effect) for effect in effects) != tuple(FrozenMap(effect) for effect in expected)
            or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != FrozenMap(schema) or not set(mechanics).issubset(mechanic_ids)):
        return ()
    try:
        _, children = validate_resolution_condition_instruction(effects[0])
        grants = (children[0], effects[0]["otherwise_effects"][0])
        if any(type(grant["schema_version"]) is not int for grant in grants):
            return ()
        stat = fixed_target_characteristics_node_capabilities(
            effects=(children[1],), target_schema=schema, mechanic_ids=BONUS_MECHANICS,
        )
    except (KeyError, TypeError, ValueError):
        return ()
    if not stat:
        return ()
    return (CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, RESOLUTION_CONDITION_CAPABILITY,
            CAST_FACT_CONDITION_CAPABILITY, SAME_TARGET_SELF_REPLACEMENT_CAPABILITY, *stat)


def targeted_turn_prohibition_program_dependencies(program):
    if not (uses_targeted_turn_prohibition((program.effects, program.handlers, program.target_schema, program.cost_schema))
            or CAPABILITY in program.capability_dependencies):
        return ()
    required = targeted_turn_prohibition_node_capabilities(program.effects, program.target_schema, program.coverage)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.trigger_limit is not None
            or program.cost_schema is not None or program.handlers):
        raise ValueError("The targeted turn prohibition requires the same spell target and its exact kicked result")
    return required


def targeted_turn_prohibition_records(stats, turn):
    raw = stats.get(WINDOW)
    if raw is None:
        return ()
    if (not isinstance(raw, Mapping) or set(raw) != {"turn_sequence", "targets"}
            or type(raw["turn_sequence"]) is not int or not 0 <= raw["turn_sequence"] <= turn
            or not isinstance(raw["targets"], list)):
        raise ValueError("The targeted turn prohibition window is malformed")
    rows = raw["targets"]
    if any(not isinstance(row, list) or len(row) != 2
           or any(type(value) is not str or not value for value in row) for row in rows):
        raise ValueError("The targeted turn prohibition needs exact battlefield identities")
    candidates = tuple(tuple(row) for row in rows)
    if candidates != tuple(sorted(set(candidates))):
        raise ValueError("The targeted turn prohibition has noncanonical battlefield identities")
    return candidates if raw["turn_sequence"] == turn else ()


def grant_targeted_turn_prohibition(stats, turn, target):
    rows = {*targeted_turn_prohibition_records(stats, turn), (target.object_id, target.logical_object_id)}
    return {"turn_sequence": turn, "targets": [list(row) for row in sorted(rows)]}


def current_targeted_turn_prohibition(host, target, acting_controller, acting_source):
    if acting_source is not None and acting_source.kind not in {"spell", "ability"}:
        return False
    key = (target.object_id, target.logical_object_id)
    return any(seat != acting_controller and key in targeted_turn_prohibition_records(player.stats, host.state.turn_sequence)
               for seat, player in host.state.players.items())
