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
    _spec, zones = decode_named_zone_selection(effect)
    refs = {row if isinstance(row, str) else row["id"] for row in options}
    return int("graveyard" in zones and any(host.state.cards[oid].ref in refs
        for oid in host.state.players[seat].zones["graveyard"]))


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
