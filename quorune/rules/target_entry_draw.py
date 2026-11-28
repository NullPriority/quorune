"""The source entry that draws first and offers its announced opponent a draw."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


TARGET_ENTRY_DRAW_VERSION = 7
TARGET_ENTRY_DRAW_CAPABILITY = "resolution.draw.source_entry_target_opponent"
_VERSION_FIELD = "event_optional_draw_version"
_ENTRY_EFFECTS = (
    {"op": "draw", "player": "$controller", "count": 1, "private": True},
    {"op": "offer_draw", "player": "$target.0", "drawer": "$target.0",
     "count": 1, "private": True, _VERSION_FIELD: TARGET_ENTRY_DRAW_VERSION},
)


def target_entry_draw_effects():
    return tuple(dict(effect) for effect in _ENTRY_EFFECTS)


def target_entry_draw_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


def uses_target_entry_draw(effect):
    return isinstance(effect, Mapping) and effect.get(_VERSION_FIELD) == TARGET_ENTRY_DRAW_VERSION


def target_entry_draw_payload_dependencies(effect, *, materialized=False):
    expected = dict(_ENTRY_EFFECTS[1])
    if materialized:
        player = effect.get("player")
        if type(player) is not str or not player or player.startswith("$"):
            raise ValueError("An optional entry draw requires the materialized opponent")
        expected.update(player=player, drawer=player)
    if (effect != expected or type(effect.get(_VERSION_FIELD)) is not int
            or type(effect.get("count")) is not int or effect.get("private") is not True):
        raise ValueError("The optional entry offer must match the single announced opponent")
    return (TARGET_ENTRY_DRAW_CAPABILITY, "resolution.event_player.optional_draw", "zone.draw.library_to_hand",
            "target.revalidate_resolution")


def target_entry_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 2 or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap(target_entry_draw_schema())
            or type(target_schema.get("count")) is not int
            or "fixed-event-player-optional-draw" not in mechanic_ids
            or FrozenMap(effects[0]) != FrozenMap(_ENTRY_EFFECTS[0])
            or type(effects[0].get("count")) is not int or effects[0].get("private") is not True):
        return ()
    try:
        return (*target_entry_draw_payload_dependencies(effects[1]),
                "trigger.event.normalized_zone_change", "trigger.placement.apnap")
    except (TypeError, ValueError):
        return ()


def target_entry_draw_program_dependencies(program):
    occurrences = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                        if uses_target_entry_draw(effect))
    if not occurrences and TARGET_ENTRY_DRAW_CAPABILITY not in program.capability_dependencies:
        return ()
    required = target_entry_draw_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (len(occurrences) != 1 or not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None or program.handlers
            or not program.ability_id.startswith("trigger:") or program.trigger_limit is not None):
        raise ValueError("The controller draw and target opponent offer require one complete source entry trigger")
    return required
