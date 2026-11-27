"""Admission and replay guards for cast-X library/graveyard creature search."""

from ..cast_x_search_model import (
    CAST_X_ZONE_SEARCH_CAPABILITY, CAST_X_ZONE_SEARCH_MECHANIC, CAST_X_ZONE_SEARCH_OPERATION,
    CAST_X_ZONE_SEARCH_VERSION, CastXZoneSearchSelection, cast_x_zone_search_pump,
    decode_cast_x_zone_search,
)
from ..errors import GameRuleError
from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


def uses_cast_x_zone_search(effect):
    return effect.get("op") == "search" and effect.get("schema_version") == CAST_X_ZONE_SEARCH_VERSION


def _is_exact_team_pump(effect):
    threshold = effect.get("minimum_amount")
    return (type(threshold) is int and threshold >= 1
            and thaw_value(effect) == cast_x_zone_search_pump(threshold))


def cast_x_zone_search_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (CAST_X_ZONE_SEARCH_MECHANIC not in mechanic_ids or target_schema is not None
            or len(effects) != 2 or not _is_exact_team_pump(effects[1])):
        return ()
    try:
        decode_cast_x_zone_search(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return (CAST_X_ZONE_SEARCH_CAPABILITY, DECLARED_AMOUNT_CAPABILITY, "library.search.restriction.static",
            "variant.commander.zone_return", "zone.change.destination_replacement",
            "continuous.resolution.fixed_characteristics_until_end_of_turn")


def cast_x_zone_search_program_dependencies(program):
    if CAST_X_ZONE_SEARCH_MECHANIC not in program.coverage and not any(
        effect.get("op") == CAST_X_ZONE_SEARCH_OPERATION or uses_cast_x_zone_search(effect)
        for effect in nested_effects((*program.effects, *program.handlers))
    ):
        return ()
    required = cast_x_zone_search_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.event != "resolve" or not program.ability_id.startswith("spell:")
            or program.event_condition is not None or program.handlers or program.destination != "graveyard"):
        raise ValueError("Announced-X zone search requires its complete resolving spell and ordered team bonus")
    return required


def is_closed_cast_x_zone_search_program(program):
    try:
        required = cast_x_zone_search_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def verify_resolving_cast_x_zone_search(host, item, effect):
    try:
        search = CastXZoneSearchSelection.from_effect(effect)
        program = host.semantics.get(item.semantic_key)
        bound = 0 if item.x_value is None else item.x_value
        if (type(bound) is not int or search.maximum != bound or item.kind not in {"spell", "spell_copy"}
                or item.context.get("currently_resolving") is not True or program is None
                or not host.semantic_program_is_current_trusted(program)
                or not is_closed_cast_x_zone_search_program(program)):
            raise ValueError("Announced-X search must keep the original resolving spell and X")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
