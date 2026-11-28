from __future__ import annotations

"""One source-entry search bounded by its controller's current black devotion."""

from ..devotion import DEVOTION_CAPABILITY, current_devotion
from ..library_search_model import (
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,
    FixedCountedLibrarySearchTemplate,
)
from ..replacement.immutable import FrozenMap, thaw_value

CAPABILITY = "library.search.controller_black_devotion_mana_value"
BOUND_FIELD = "controller_black_devotion_mana_value_version"
TEMPLATE = "entry-black-devotion-search-life-loss-v1"
SEQUENCE = "fixed-effect-clause-sequence"
LIFE = "cr-119-life"
_TEXT = (
    "search your library for a card with mana value less than or equal to your devotion to black, "
    "reveal it, put it into your hand, then shuffle. you lose 3 life."
)


def _fixed_search(maximum):
    return FixedCountedLibrarySearchTemplate(
        1, False, FrozenMap({"mana_value": {"maximum": maximum}}), "hand", True,
    ).effect()


def _entry_effects():
    return (
        {**_fixed_search(0), BOUND_FIELD: 1},
        {"op": "lose_life", "player": "$controller", "amount": 3},
    )


def black_devotion_search_result(body):
    reminder = " (each {b} in the mana costs of permanents you control counts toward your devotion to black.)"
    if " ".join(body.casefold().split()).removesuffix(reminder) != _TEXT:
        return None
    return TEMPLATE, _entry_effects(), None, (SEQUENCE, FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID, LIFE)


def black_devotion_search_capabilities(effect, target_schema):
    if (target_schema is not None or type(effect.get(BOUND_FIELD)) is not int
            or FrozenMap(effect) != FrozenMap(_entry_effects()[0])):
        return ()
    try:
        FixedCountedLibrarySearchTemplate.from_effect({key: value for key, value in effect.items() if key != BOUND_FIELD})
    except (KeyError, TypeError, ValueError):
        return ()
    return CAPABILITY, DEVOTION_CAPABILITY, FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID


def black_devotion_search_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key
    from .fixed_effect_clause_shapes import fixed_effect_clause_sequence_node_capabilities

    marked = (CAPABILITY in program.capability_dependencies or program.provenance.get("template_id") == TEMPLATE
              or contains_nested_key((program.effects, program.target_schema, program.handlers), BOUND_FIELD))
    if not marked:
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None or program.handlers
            or tuple(thaw_value(program.effects)) != _entry_effects()
            or type(program.effects[1]["amount"]) is not int
            or not black_devotion_search_capabilities(program.effects[0], program.target_schema)):
        raise ValueError("Black devotion search requires the complete source-entry search and life loss")
    required = fixed_effect_clause_sequence_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Black devotion search requires its ordered search and life owners")
    return (*required, "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def bind_black_devotion_search(host, item, effect, *, instruction_pointer):
    if (not black_devotion_search_capabilities(effect, None) or instruction_pointer != 0
            or item.kind != "triggered_ability" or item.context.get("event") != "permanent.enter"
            or item.context.get("currently_resolving") is not True or item.targets
            or not item.source_object_id or not item.context.get("source_logical_object_id")):
        raise ValueError("Black devotion search requires the first instruction of its resolving entry trigger")
    return _fixed_search(current_devotion(host, item.controller, ("B",)))
