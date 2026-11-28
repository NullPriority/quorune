"""Check owner library placement choices and ordered source results."""

from collections.abc import Mapping

from ..public_zone_moves import (
    FIXED_OWNER_ZONE_MOVE_MECHANIC, OWNER_LIBRARY_POSITION_CAPABILITY,
    OWNER_LIBRARY_POSITION_MECHANIC, OWNER_LIBRARY_POSITION_OPERATION,
)
from ..replacement.immutable import freeze_value, thaw_value
from ..target_controller_information import (
    TARGET_OWNER_INFORMATION_CAPABILITY, TARGET_OWNER_INFORMATION_MECHANIC, owner_reference_index,
)
from .random_activation_target import random_activation_has_tap_price as has_exact_tap_price


DRAW_SOURCE_OWNER_TOP_FIELD = 'draw_source_owner_top_version'
DRAW_SOURCE_OWNER_TOP_CAPABILITY = 'zone.library_top.source_after_private_draw'
DRAW_SOURCE_OWNER_TOP_MECHANIC = 'private-draw-then-source-owner-top'
_PRIVATE_DRAW = {'op': 'draw', 'player': '$controller', 'count': 1, 'private': True}
_SOURCE_OWNER_TOP = {'op': 'move', 'card': '$source.zone_object', 'destination': 'library', 'position': 'top'}


def draw_source_owner_top_effects():
    return dict(_PRIVATE_DRAW), {**_SOURCE_OWNER_TOP, DRAW_SOURCE_OWNER_TOP_FIELD: 1}


def _is_draw_source_owner_top_move(effect):
    return isinstance(effect, Mapping) and DRAW_SOURCE_OWNER_TOP_FIELD in effect


def _valid_source_move(effect):
    return (type(effect.get(DRAW_SOURCE_OWNER_TOP_FIELD)) is int
            and effect == draw_source_owner_top_effects()[1])


def draw_source_owner_top_capabilities(effects, target_schema, active_zone, cost_schema, mechanics):
    if (len(effects) != 2 or target_schema is not None or active_zone != 'battlefield'
            or DRAW_SOURCE_OWNER_TOP_MECHANIC not in mechanics or not has_exact_tap_price(cost_schema)):
        return ()
    draw, move = effects
    if (draw != _PRIVATE_DRAW or type(draw.get('count')) is not int or draw.get('private') is not True
            or not _valid_source_move(move)):
        return ()
    from .public_zone_move_capability_shapes import fixed_owner_zone_move_node_capabilities

    required = fixed_owner_zone_move_node_capabilities(
        effects=(_SOURCE_OWNER_TOP,), target_schema=None, mechanic_ids=(FIXED_OWNER_ZONE_MOVE_MECHANIC,),
    )
    return (*required, DRAW_SOURCE_OWNER_TOP_CAPABILITY, 'zone.draw.library_to_hand') if required else ()


def bind_draw_source_owner_top_move(item, effect):
    if not _is_draw_source_owner_top_move(effect):
        return effect
    if not _valid_source_move(effect) or item.kind != 'activated_ability':
        raise ValueError('The source library move requires its original activated result')
    return dict(_SOURCE_OWNER_TOP)


def is_closed_draw_source_owner_top_program(program):
    if DRAW_SOURCE_OWNER_TOP_CAPABILITY not in program.capability_dependencies:
        return False
    try:
        required = owner_library_position_runtime_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


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
    from .ordered_source_owner_hand_entry import ordered_source_owner_hand_entry_dependencies

    if required := ordered_source_owner_hand_entry_dependencies(program):
        return required
    from .effect_shape_walkers import nested_effects
    marked = [effect for effect in nested_effects((*program.effects, *getattr(program, 'handlers', ())))
              if _is_draw_source_owner_top_move(effect)]
    if marked or DRAW_SOURCE_OWNER_TOP_CAPABILITY in program.capability_dependencies:
        required = draw_source_owner_top_capabilities(program.effects, program.target_schema, program.active_zone,
                                                     program.cost_schema, program.coverage)
        if (not required or len(marked) != 1 or program.event != 'activate' or program.event_condition is not None
                or program.provenance.get('granted_only') is True or len(program.handlers) != 1):
            raise ValueError('Draw and source placement require the original ordinary tap activation')
        from ..compiler.activated_costs import activated_ability_cost
        from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
        if (ability.builtin_semantic_key != program.key or ability.zones != ('battlefield',) or ability.mana_ability
                or ability.target_schema is not None or not has_exact_tap_price(activated_ability_cost(ability))):
            raise ValueError('The activation catalog changed the source placement price or target scope')
        return required

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
