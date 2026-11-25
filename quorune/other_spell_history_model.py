"""Closed cast-history facts for one other spell and its fixed private draw."""

from collections.abc import Mapping

from .public_history_query_model import (
    PUBLIC_HISTORY_QUERY_CAPABILITY, PublicHistoryQuerySpec, matching_public_history_events,
)
from .replacement.immutable import FrozenMap, thaw_value


OTHER_SPELL_CAST_FIELD = "other_spell_cast_this_turn"
OTHER_SPELL_CAST_MECHANIC = "other-spell-cast-this-turn"
OTHER_SPELL_DRAW_VERSION = 22
OTHER_SPELL_DRAW_CONDITION = {"kind": OTHER_SPELL_CAST_FIELD, "schema_version": 1}
_FIELDS = frozenset({
    "op", "schema_version", "player", "condition", "cast_fact", "effects",
    "mechanic_ids", "prefix_mechanic_ids",
})
_CONTROLLER_CASTS = PublicHistoryQuerySpec(event="spell_cast")


def is_other_spell_self_cast_condition(condition):
    if not isinstance(condition, Mapping) or set(condition) != {"all"}:
        return False
    leaves = condition["all"]
    if not isinstance(leaves, (list, tuple)) or len(leaves) != 3:
        return False
    return thaw_value(FrozenMap(condition)) == {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "card", "op": "eq", "value": "$source.ref"},
        {"field": "caster_spell_number", "op": "gte", "value": 2},
    ]} and type(leaves[-1]["value"]) is int


def other_spell_self_cast_condition_capabilities(condition):
    if not is_other_spell_self_cast_condition(condition):
        return ()
    return (PUBLIC_HISTORY_QUERY_CAPABILITY, "trigger.condition.fixed_public_state")


def other_spell_was_cast_this_turn(state, *, player, excluded_incarnation):
    """Check this player's committed casts apart from the specified stack incarnation."""
    if type(player) is not str or player not in state.players:
        raise ValueError("The other-spell query requires its exact player")
    if type(excluded_incarnation) is not str or not excluded_incarnation or excluded_incarnation.startswith("$"):
        raise ValueError("The other-spell query requires the exact referenced spell incarnation")
    events = matching_public_history_events(state, player, _CONTROLLER_CASTS)
    if events is None or any(type(entry.object_incarnation) is not str or not entry.object_incarnation
                             for entry in events):
        raise ValueError("The other-spell query requires current committed cast identities")
    return any(entry.object_incarnation != excluded_incarnation for entry in events)


def is_other_spell_draw(effect):
    return (
        isinstance(effect, Mapping) and effect.get("op") == "apply_if_public_condition"
        and effect.get("schema_version") == OTHER_SPELL_DRAW_VERSION
    )


def validate_other_spell_draw(effect, *, bound=False):
    if (not is_other_spell_draw(effect) or set(effect) != _FIELDS
            or type(effect["schema_version"]) is not int):
        raise ValueError("The other-spell draw requires its exact instruction version")
    condition = effect["condition"]
    if (not isinstance(condition, Mapping) or dict(condition) != OTHER_SPELL_DRAW_CONDITION
            or type(condition["schema_version"]) is not int):
        raise ValueError("The other-spell draw requires its exact current-turn condition")
    actor, matched = effect["player"], effect["cast_fact"]
    if bound:
        if type(actor) is not str or not actor or actor.startswith("$") or type(matched) is not bool:
            raise ValueError("The other-spell draw requires the resolving controller and a committed cast fact")
    elif actor != "$controller" or matched is not None:
        raise ValueError("The other-spell draw requires the stack owner to bind its cast fact")
    results = effect["effects"]
    if (not isinstance(results, (list, tuple)) or len(results) != 1 or not isinstance(results[0], Mapping)
            or thaw_value(FrozenMap(results[0])) != {"op": "draw", "player": actor, "count": 1, "private": True}
            or type(results[0]["count"]) is not int or results[0]["private"] is not True):
        raise ValueError("The other-spell result permits one private controller draw")
    for field in ("mechanic_ids", "prefix_mechanic_ids"):
        names = effect[field]
        if (not isinstance(names, (list, tuple)) or not names
                or any(type(name) is not str or not name for name in names) or len(set(names)) != len(names)):
            raise ValueError("The other-spell draw requires complete fixed component mechanics")
    return tuple(results)
