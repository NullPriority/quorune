"""One mandatory announced-X damage result followed by the independent hand cast."""

from __future__ import annotations

from ..bounded_hand_cast_model import bounded_hand_cast_payload_dependencies, is_announced_x_hand_cast_effect
from ..query_effect_amount_model import CastXAmountSpec
from ..replacement.immutable import FrozenMap
from .damage_capability_shapes import _FIXED_DAMAGE_TARGET_SCHEMAS
from .effect_shape_walkers import nested_effects


TEMPLATE = "announced-x-damage-then-bounded-hand-cast-v1"
_DAMAGE_MECHANICS = ("declared-effect-amount", "cr-115-targets", "cr-120-damage")


def announced_x_damage_hand_cast_node_capabilities(effects, target_schema, cost_schema):
    if not any(is_announced_x_hand_cast_effect(effect) for effect in effects):
        return None
    if (len(effects) != 2 or cost_schema is not None
            or FrozenMap(target_schema or {}) != FrozenMap(_FIXED_DAMAGE_TARGET_SCHEMAS["any_target"])
            or type(target_schema.get("count")) is not int):
        return ()
    damage, permission = effects
    if not is_announced_x_hand_cast_effect(permission):
        return ()
    expected = {"op": "damage", "source": "$source", "target": "$target.0", "amount": CastXAmountSpec().to_dict()}
    if FrozenMap(damage) != FrozenMap(expected):
        return ()
    from .capabilities import capability_dependencies_for_node

    try:
        CastXAmountSpec.from_dict(damage["amount"])
        bounded = bounded_hand_cast_payload_dependencies(permission)
        damage_dependencies = capability_dependencies_for_node(
            effects=(damage,), target_schema=target_schema, mechanic_ids=_DAMAGE_MECHANICS,
        )
    except (TypeError, ValueError, KeyError):
        return ()
    return tuple(sorted({*damage_dependencies, *bounded})) if damage_dependencies and bounded else ()


def announced_x_damage_hand_cast_program_dependencies(program):
    if not any(is_announced_x_hand_cast_effect(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = announced_x_damage_hand_cast_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
            or program.event_condition is not None):
        raise ValueError("An announced-X hand cast requires its original mandatory damage and complete resolving spell")
    return required


def is_closed_announced_x_damage_hand_cast_program(program):
    try:
        required = announced_x_damage_hand_cast_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
