"""Closed follow-up for a publicly revealed controller library creature."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap


REVEALED_CREATURE_OPERATION = "reveal_controller_library_top_creature_statistics"
REVEALED_CREATURE_MECHANIC = "public-library-top-creature-statistics"
REVEALED_CREATURE_CAPABILITY = "library.top.reveal_controller_creature_statistics"
REVEALED_CREATURE_DEPENDENCIES = (
    REVEALED_CREATURE_CAPABILITY,
    "life.change.effect",
    "zone.draw.library_to_hand",
)


def revealed_creature_effect(*, player="$controller"):
    return {"op": REVEALED_CREATURE_OPERATION, "schema_version": 1, "player": player}


def revealed_creature_dependencies(effect, *, player="$controller"):
    if (not isinstance(effect, Mapping) or effect != revealed_creature_effect(player=player)
            or type(effect["schema_version"]) is not int):
        raise ValueError("A revealed creature result needs its closed controller descriptor")
    return REVEALED_CREATURE_DEPENDENCIES


def scry_revealed_creature_capabilities(*, effects, target_schema, mechanic_ids):
    from .node_capability_shapes import fixed_scry_node_capabilities

    if target_schema is not None or len(effects) != 2:
        return ()
    scry = fixed_scry_node_capabilities(
        effects=effects[:1], target_schema=None, mechanic_ids=mechanic_ids,
    )
    if not scry:
        return ()
    try:
        return (*scry, *revealed_creature_dependencies(effects[1]))
    except ValueError:
        return ()


def revealed_creature_followups(row, *, player):
    """Bind both quantities from the same card before drawing can move it."""
    if "creature" not in row.types:
        return ()
    power, toughness = row.effective_power, row.effective_toughness
    if type(power) is not int or type(toughness) is not int:
        raise ValueError("The revealed creature's power and toughness must both be represented")
    effects = []
    if power > 0:
        effects.append(FrozenMap({"op": "draw", "player": player, "count": power, "private": True}))
    if toughness > 0:
        effects.append(FrozenMap({"op": "life", "player": player, "delta": toughness, "source": "$source"}))
    return tuple(effects)
