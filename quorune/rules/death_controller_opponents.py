from __future__ import annotations

"""Closed fixed draw-then-gain result for opponents of a dying creature's controller."""

from collections.abc import Mapping

from ..counter_names import normalized_counter_name
from ..replacement.immutable import freeze_value, thaw_value
from .effect_shape_walkers import contains_aftermath_kind


DEATH_OPPONENTS_CAPABILITY = "resolution.event_player.death_controller_opponents"
DEATH_OPPONENTS_KIND = "death_controller_opponents"
_RESULT_OWNERS = (DEATH_OPPONENTS_CAPABILITY, "life.change.effect", "zone.draw.library_to_hand")
_EVENT_OWNERS = (
    "trigger.effect.fixed_event",
    "trigger.event.counter_qualified_zone_change",
    "trigger.event.normalized_zone_change",
    "trigger.event.qualified_zone_change",
    "trigger.placement.apnap",
)
_PLAYER_SET = {"kind": DEATH_OPPONENTS_KIND, "schema_version": 1}
_CREATURE = {"field": "types", "op": "contains_any", "value": ["creature"]}


def death_opponents_condition_is_closed(condition):
    """Require one fixed named counter on a creature in the existing death event."""
    raw = thaw_value(freeze_value(condition))
    if not isinstance(raw, Mapping) or set(raw) != {"all"}:
        return False
    terms = raw["all"]
    if not isinstance(terms, list) or len(terms) != 2 or terms[0] != _CREATURE:
        return False
    counter = terms[1]
    if (not isinstance(counter, Mapping) or set(counter) != {"field", "op", "value"}
            or (counter["field"], counter["op"]) != ("counter_names", "contains_any")):
        return False
    names = counter["value"]
    if not isinstance(names, list) or len(names) != 1 or type(names[0]) is not str:
        return False
    name = names[0]
    return bool(name.strip()) and name == normalized_counter_name(name)


def death_opponent_effects(draw, life):
    if any(type(amount) is not int or amount <= 0 for amount in (draw, life)):
        raise ValueError("Death-controller opponents require positive fixed draw and life amounts")
    return (
        {"op": "draw_each_player", "players": dict(_PLAYER_SET), "count": draw, "private": True},
        {"op": "life", "players": dict(_PLAYER_SET), "delta": life},
    )


def uses_death_opponent_results(effects):
    return contains_aftermath_kind(effects, DEATH_OPPONENTS_KIND)


def death_opponent_node_capabilities(effects, target_schema):
    if target_schema is not None or len(effects) != 2:
        return ()
    try:
        expected = death_opponent_effects(effects[0].get("count"), effects[1].get("delta"))
    except (AttributeError, ValueError):
        return ()
    raw = tuple(thaw_value(freeze_value(effect)) for effect in effects)
    if raw != expected or any(type(effect["players"]["schema_version"]) is not int for effect in raw):
        return ()
    if raw[0]["private"] is not True:
        return ()
    return _RESULT_OWNERS


def death_opponents_program_dependencies(program):
    applies = uses_death_opponent_results(
        (program.effects, program.handlers, program.target_schema, program.cost_schema),
    )
    if not applies:
        return ()
    required = death_opponent_node_capabilities(program.effects, program.target_schema)
    if (not required or (program.event, program.active_zone) != ("creature.dies", "battlefield")
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
            or not death_opponents_condition_is_closed(program.event_condition)
            or "one_or_more_event_batch" in program.coverage):
        raise ValueError("Death-controller opponents require a complete untargeted, singular counter-qualified death")
    return tuple(sorted((*required, *_EVENT_OWNERS)))


def bind_death_opponent_result(host, item, effect):
    """Use sealed public departure facts, then project to ordinary ordered draw and life owners."""
    recipient = effect.get("players")
    if not isinstance(recipient, Mapping) or recipient.get("kind") != DEATH_OPPONENTS_KIND:
        return effect
    operation = effect.get("op")
    if operation == "draw_each_player":
        expected = death_opponent_effects(effect.get("count"), 1)[0]
        if effect.get("private") is not True:
            raise ValueError("Death-controller opponents draw in private")
    elif operation == "life":
        expected = death_opponent_effects(1, effect.get("delta"))[1]
    else:
        raise ValueError("Death-controller opponents require an ordered draw and life result")
    if thaw_value(freeze_value(effect)) != expected or type(recipient.get("schema_version")) is not int:
        raise ValueError("Death-controller opponents have a closed result shape")
    context = item.context
    anchor = context.get("previous_controller")
    counters = context.get("counter_names")
    if ((context.get("event"), context.get("from"), context.get("to"))
            != ("creature.dies", "battlefield", "graveyard")
            or type(anchor) is not str or anchor not in host.state.players
            or not isinstance(counters, (list, tuple)) or not counters):
        raise ValueError("Death-controller opponents lost the original battlefield controller or counter facts")
    players = [seat for seat in host.apnap_order() if seat != anchor]
    result = {**effect, "players": players}
    if operation == "life":
        from .public_life_gain_scopes import PUBLIC_LIFE_GAIN_FIELD, SELECTED_PUBLIC_LIFE_GAIN_VERSION

        result[PUBLIC_LIFE_GAIN_FIELD] = SELECTED_PUBLIC_LIFE_GAIN_VERSION
    return result
