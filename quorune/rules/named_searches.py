"""Close the named search and every original preceding result separately."""

from ..named_search_model import (
    NAMED_ZONE_SEARCH_CAPABILITY, NAMED_ZONE_SEARCH_MECHANIC, NAMED_ZONE_SEARCH_OPERATION,
    decode_named_zone_search, decode_named_zone_selection,
)


def named_zone_search_instruction_capabilities(effect):
    if effect.get("op") == "search":
        decode_named_zone_selection(effect)
    else:
        decode_named_zone_search(effect)
    return (NAMED_ZONE_SEARCH_CAPABILITY, "library.search.restriction.static",
            "variant.commander.zone_return", "zone.change.destination_replacement")


def named_zone_search_minimum(host, seat, effect, options):
    """CR 701.23b's Splinter example: a public match cannot be hidden failure."""
    from ..library_search_model import SACRIFICE_BOUND_CREATURE_SEARCH_VERSION

    if effect.get("schema_version") == SACRIFICE_BOUND_CREATURE_SEARCH_VERSION:
        zones = sacrifice_bound_creature_search_zones(effect)
    else:
        _spec, zones = decode_named_zone_selection(effect)
    from .library_search_rules import public_graveyard_search_minimum

    return public_graveyard_search_minimum(host, seat, zones, options)


def named_zone_search_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (NAMED_ZONE_SEARCH_MECHANIC not in mechanic_ids or not 1 <= len(effects) <= 8
            or sum(effect.get("op") == NAMED_ZONE_SEARCH_OPERATION for effect in effects) != 1
            or effects[-1].get("op") != NAMED_ZONE_SEARCH_OPERATION):
        return ()
    try:
        owners = set(named_zone_search_instruction_capabilities(effects[-1]))
    except (TypeError, ValueError, KeyError):
        return ()
    if len(effects) == 1:
        return tuple(sorted(owners)) if target_schema is None else ()
    from .capabilities import capability_dependencies_for_node
    prefix = capability_dependencies_for_node(effects=effects[:-1], target_schema=target_schema,
        mechanic_ids=tuple(m for m in mechanic_ids if m != NAMED_ZONE_SEARCH_MECHANIC))
    return tuple(sorted(owners | set(prefix))) if prefix else ()


def is_closed_named_zone_search_program(program):
    owners = named_zone_search_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(owners) and set(owners) <= set(program.capability_dependencies)



SACRIFICE_BOUND_CREATURE_SEARCH_CAPABILITY = "library.search.creature_bound_by_committed_sacrifices"
SACRIFICE_BOUND_CREATURE_RECIPE_FIELD = "creature_search_from_committed_sacrifices"
_SACRIFICE_SEARCH_ZONES = {"library": ("library",), "graveyard": ("graveyard",), "both": ("library", "graveyard")}


def sacrifice_bound_creature_search_recipe(*, actor="$controller"):
    return {"op": "search", "player": actor, SACRIFICE_BOUND_CREATURE_RECIPE_FIELD: 1}


def validate_sacrifice_bound_creature_search_recipe(value, *, actor="$controller"):
    if (value != sacrifice_bound_creature_search_recipe(actor=actor)
            or type(value.get(SACRIFICE_BOUND_CREATURE_RECIPE_FIELD)) is not int):
        raise ValueError("The committed sacrifice recipe must preserve the original creature search")


def sacrifice_bound_creature_search_instruction(zones, count):
    from ..library_search_model import SACRIFICE_BOUND_CREATURE_SEARCH_VERSION

    if type(count) is not int or count < 0 or zones not in _SACRIFICE_SEARCH_ZONES.values():
        raise ValueError("The sacrifice search needs its exact zone choice and committed amount")
    return {
        "op": "search", "schema_version": SACRIFICE_BOUND_CREATURE_SEARCH_VERSION, "zone": list(zones),
        "selector": {"types": ["creature"], "mana_value": {"maximum": count}},
        "count": {"minimum": 1, "maximum": 1}, "destination": "battlefield",
        "reveal": False, "shuffle_after": "library" in zones,
    }


def sacrifice_bound_creature_search_zones(effect):
    from collections.abc import Mapping

    from ..replacement.immutable import FrozenMap

    zones = effect.get("zone")
    selector = effect.get("selector")
    bound = selector.get("mana_value") if isinstance(selector, Mapping) else None
    raw_count = effect.get("count")
    if (not isinstance(zones, (list, tuple)) or not isinstance(bound, Mapping)
            or not isinstance(raw_count, Mapping) or type(effect.get("schema_version")) is not int
            or effect.get("reveal") is not False or type(effect.get("shuffle_after")) is not bool
            or type(raw_count.get("minimum")) is not int or type(raw_count.get("maximum")) is not int):
        raise ValueError("The committed sacrifice search descriptor is malformed")
    expected = sacrifice_bound_creature_search_instruction(tuple(zones), bound.get("maximum"))
    if FrozenMap(effect) != FrozenMap(expected):
        raise ValueError("The committed sacrifice search changed its full result")
    return tuple(zones)


def has_public_zone_search_minimum(effect):
    from ..library_search_model import SACRIFICE_BOUND_CREATURE_SEARCH_VERSION

    return effect.get("schema_version") in (6, SACRIFICE_BOUND_CREATURE_SEARCH_VERSION)


def committed_sacrifice_creature_search_choice(host, actor, count):
    from .library_search_rules import library_search_constraint

    if actor not in host.active_seats:
        raise ValueError("A committed sacrifice search requires its original resolving controller")
    allowed = ("graveyard",) if library_search_constraint(host, actor).prohibited else ("library", "graveyard", "both")
    options = [{"id": choice, "label": "Library and graveyard" if choice == "both" else choice.title()}
               for choice in allowed]
    return {"op": "choose_option", "player": actor, "prompt": "Choose where to search for a creature card.",
            "options": options, "then_by_choice": {
                choice: [sacrifice_bound_creature_search_instruction(_SACRIFICE_SEARCH_ZONES[choice], count)]
                for choice in allowed
            }}
