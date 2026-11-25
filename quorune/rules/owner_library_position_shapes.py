"""Check both move outcomes of the announced target's owner choice."""

from collections.abc import Mapping

from ..public_zone_moves import (
    FIXED_OWNER_ZONE_MOVE_MECHANIC, OWNER_LIBRARY_POSITION_CAPABILITY,
    OWNER_LIBRARY_POSITION_MECHANIC, OWNER_LIBRARY_POSITION_OPERATION,
)
from ..replacement.immutable import freeze_value, thaw_value
from ..target_controller_information import (
    TARGET_OWNER_INFORMATION_CAPABILITY, TARGET_OWNER_INFORMATION_MECHANIC, owner_reference_index,
)


def owner_library_position_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or not isinstance(target_schema, Mapping):
        return ()
    schema = thaw_value(freeze_value(target_schema))
    if schema.get("zones") != ["battlefield"]:
        return ()
    mechanics = set(mechanic_ids)
    if not {OWNER_LIBRARY_POSITION_MECHANIC, TARGET_OWNER_INFORMATION_MECHANIC,
            FIXED_OWNER_ZONE_MOVE_MECHANIC, "cr-115-targets"} <= mechanics:
        return ()
    leaf, = effects
    if (set(leaf) != {"op", "schema_version", "card", "player"}
            or leaf["op"] != OWNER_LIBRARY_POSITION_OPERATION
            or type(leaf["schema_version"]) is not int or leaf["schema_version"] != 1
            or leaf["card"] != "$target.0"):
        return ()
    try:
        if owner_reference_index(leaf["player"]) != 0:
            return ()
    except (TypeError, ValueError):
        return ()
    from .public_zone_move_capability_shapes import fixed_owner_zone_move_node_capabilities

    owners = set()
    for position in ("top", "bottom"):
        move = {"op": "move", "card": "$target.0", "destination": "library", "position": position}
        required = fixed_owner_zone_move_node_capabilities(
            effects=(move,), target_schema=schema, mechanic_ids=mechanics,
        )
        if not required:
            return ()
        owners.update(required)
    return tuple(sorted(owners | {OWNER_LIBRARY_POSITION_CAPABILITY, TARGET_OWNER_INFORMATION_CAPABILITY,
                                  "target.revalidate_resolution"}))


def owner_library_position_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    positions = [effect for effect in nested_effects((*program.effects, *getattr(program, "handlers", ())))
                 if effect.get("op") == OWNER_LIBRARY_POSITION_OPERATION]
    if not positions:
        return ()
    if len(positions) != 1 or not program.effects or positions[0] != program.effects[0]:
        raise ValueError("Owner library placement requires one original announced target")
    required = owner_library_position_node_capabilities(
        effects=(program.effects[0],), target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Owner library placement lacks its complete target and choice authority")
    return required
