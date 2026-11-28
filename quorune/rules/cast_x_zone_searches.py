"""Admission and replay guards for cast-X library/graveyard creature search."""

from ..cast_x_search_model import (
    CAST_X_ZONE_SEARCH_CAPABILITY, CAST_X_ZONE_SEARCH_MECHANIC, CAST_X_ZONE_SEARCH_OPERATION,
    CAST_X_ZONE_SEARCH_VERSION, CastXZoneSearchSelection, cast_x_zone_search_pump,
    ENTRY_X_ZONE_SEARCH_CAPABILITY, ENTRY_X_ZONE_SEARCH_MECHANIC, decode_zone_search,
)
from ..entry_x_amount_model import ENTRY_X_AMOUNT_CAPABILITY, EntryXAmountSpec, resolve_entry_x_amount
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
    if target_schema is not None or not effects:
        return ()
    try:
        _, from_entry = decode_zone_search(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    shared = (CAST_X_ZONE_SEARCH_CAPABILITY, "library.search.restriction.static",
              "variant.commander.zone_return", "zone.change.destination_replacement")
    if from_entry:
        if len(effects) != 1 or ENTRY_X_ZONE_SEARCH_MECHANIC not in mechanic_ids:
            return ()
        return (*shared, ENTRY_X_ZONE_SEARCH_CAPABILITY, ENTRY_X_AMOUNT_CAPABILITY,
                "trigger.effect.fixed_event", "trigger.event.normalized_zone_change", "trigger.placement.apnap")
    if (len(effects) != 2 or CAST_X_ZONE_SEARCH_MECHANIC not in mechanic_ids
            or not _is_exact_team_pump(effects[1])):
        return ()
    return (*shared, DECLARED_AMOUNT_CAPABILITY, "continuous.resolution.fixed_characteristics_until_end_of_turn")


def cast_x_zone_search_program_dependencies(program):
    if not {CAST_X_ZONE_SEARCH_MECHANIC, ENTRY_X_ZONE_SEARCH_MECHANIC}.intersection(program.coverage) and not any(
        effect.get("op") == CAST_X_ZONE_SEARCH_OPERATION or uses_cast_x_zone_search(effect)
        for effect in nested_effects((*program.effects, *program.handlers))
    ):
        return ()
    required = cast_x_zone_search_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if required and ENTRY_X_ZONE_SEARCH_CAPABILITY in required:
        if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
                or not program.ability_id.startswith("trigger:") or program.event_condition is not None
                or program.handlers or program.cost_schema is not None or program.destination is not None):
            raise ValueError("Entry-X zone search requires its exact ordinary self-entry ability")
        return required
    if (not required or program.event != "resolve" or not program.ability_id.startswith("spell:")
            or program.event_condition is not None or program.handlers or program.destination != "graveyard"):
        raise ValueError("Announced-X zone search requires its complete resolving spell and ordered team bonus")
    return required


def is_closed_x_zone_search_program(program):
    try:
        required = cast_x_zone_search_program_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def is_closed_cast_x_zone_search_program(program):
    return program.event == "resolve" and is_closed_x_zone_search_program(program)


def verify_resolving_cast_x_zone_search(host, item, effect):
    try:
        search = CastXZoneSearchSelection.from_effect(effect)
        program = host.semantics.get(item.semantic_key)
        if (item.context.get("currently_resolving") is not True or program is None
                or not host.semantic_program_is_current_trusted(program)
                or not is_closed_x_zone_search_program(program)):
            raise ValueError("X-bound search requires its original trusted resolving ability")
        from_entry = program.event == "permanent.enter.self"
        if from_entry:
            bound = resolve_entry_x_amount(EntryXAmountSpec().to_dict(), item)
            legal_kind = item.kind == "triggered_ability"
        else:
            bound = 0 if item.x_value is None else item.x_value
            legal_kind = item.kind in {"spell", "spell_copy"}
        if (not legal_kind or search.non_human is not from_entry or type(bound) is not int
                or search.maximum != bound):
            raise ValueError("X-bound search lost its original amount or selector")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
