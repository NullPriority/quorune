"""Mandatory private library search with play permission for its result."""

from collections.abc import Mapping

from ..errors import GameRuleError
from ..library_search_model import PRIVATE_SEARCH_PLAY_VERSION
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


PRIVATE_SEARCH_PLAY_MECHANIC = "entry-private-search-play"
PRIVATE_SEARCH_PLAY_CAPABILITY = "library.search.private_face_down_play"
PRIVATE_SEARCH_PLAY_DEPENDENCIES = (
    PRIVATE_SEARCH_PLAY_CAPABILITY,
    "library.search.restriction.static",
    "zone.change.destination_replacement",
)


def private_search_play_effect():
    return {
        "op": "search", "schema_version": PRIVATE_SEARCH_PLAY_VERSION, "zone": "library",
        "selector": {}, "count": {"minimum": 1, "maximum": 1}, "destination": "exile",
        "reveal": False, "shuffle_after": True,
    }


def uses_private_search_play(effect):
    return (effect.get("op") == "search"
            and effect.get("schema_version") == PRIVATE_SEARCH_PLAY_VERSION)


def validate_private_search_play(effect):
    count = effect.get("count")
    if (not isinstance(count, Mapping) or type(effect.get("schema_version")) is not int
            or any(type(value) is not int for value in count.values())
            or effect.get("reveal") is not False or effect.get("shuffle_after") is not True
            or FrozenMap(effect) != FrozenMap(private_search_play_effect())):
        raise ValueError("Private search play requires the original mandatory face-down search")


def private_search_play_node_capabilities(effects, target_schema, mechanics, cost_schema):
    if PRIVATE_SEARCH_PLAY_MECHANIC not in mechanics and not any(uses_private_search_play(effect) for effect in effects):
        return None
    if target_schema is not None or cost_schema is not None or len(effects) != 1:
        return ()
    try:
        validate_private_search_play(effects[0])
    except (TypeError, ValueError):
        return ()
    return PRIVATE_SEARCH_PLAY_DEPENDENCIES


def private_search_play_program_dependencies(program):
    marked = PRIVATE_SEARCH_PLAY_MECHANIC in program.coverage
    uses = any(uses_private_search_play(effect) for effect in nested_effects((program.effects, program.handlers)))
    if not (marked or uses):
        return ()
    owned = private_search_play_node_capabilities(
        program.effects, program.target_schema, program.coverage, program.cost_schema,
    )
    if (not owned or not marked or not program.ability_id.startswith("trigger:")
            or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.handlers or program.event_condition is not None):
        raise ValueError("Private search play requires its original untargeted self-entry trigger")
    return (*owned, "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def verify_resolving_private_search_play(host, item, effect):
    program = host.semantics.get(item.semantic_key)
    try:
        if (item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
                or program is None or not host.semantic_program_is_current_trusted(program)
                or not private_search_play_program_dependencies(program)
                or FrozenMap(effect) != FrozenMap(program.effects[0])):
            raise ValueError("Private search play lost the original resolving trigger")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
