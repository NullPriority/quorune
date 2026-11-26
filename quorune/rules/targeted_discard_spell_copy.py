from __future__ import annotations

"""Close private fixed discard followed by the affected player's resolving spell copy."""

from collections.abc import Mapping

from .affected_player_discard_capability_shapes import (
    FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC, fixed_affected_player_discard_node_capabilities,
)
from .effect_shape_walkers import nested_effects


MECHANIC = "targeted-discard-resolving-copy"
CAPABILITY = "stack.copy.targeted_fixed_discard_resolution"
COPY_OFFER_FIELD = "affected_player_resolving_copy"
RESOLVING_COPY_FIELD = "copy_from_resolving_spell"


def copy_offer(player="$target.0"):
    return {"op": "choose_objects", "player": player, COPY_OFFER_FIELD: 1}


def accepted_copy_instruction(player, stack_ref):
    return {"op": "copy_stack_item", "player": player, "stack": stack_ref, RESOLVING_COPY_FIELD: 1}


def is_copy_offer(effect, *, player="$target.0"):
    return (isinstance(effect, Mapping) and type(effect.get(COPY_OFFER_FIELD)) is int
            and dict(effect) == copy_offer(player))


def is_accepted_copy_instruction(effect, *, actor, stack_ref):
    return (type(effect.get(RESOLVING_COPY_FIELD)) is int and bool(stack_ref)
            and dict(effect) == accepted_copy_instruction(actor, stack_ref))


def _operand_dependencies(effects, target_schema):
    if len(effects) != 2 or not is_copy_offer(effects[1]):
        return ()
    discard = effects[0]
    if (not isinstance(discard, Mapping) or type(discard.get("count")) is not int or discard["count"] != 2
            or discard.get("players") != ["$target.0"]
            or discard.get("target") != "$target.0"
            or not isinstance(target_schema, Mapping) or target_schema.get("player_relation") != "any"):
        return ()
    discard_dependencies = fixed_affected_player_discard_node_capabilities(
        effects=(discard,), target_schema=target_schema,
        mechanic_ids=(FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC, "cr-402-hand", "cr-115-targets"),
    )
    return (CAPABILITY, *discard_dependencies) if discard_dependencies else ()


def targeted_discard_copy_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    return _operand_dependencies(effects, target_schema) if MECHANIC in mechanic_ids else ()


def targeted_discard_copy_runtime_dependencies(program):
    instructions = tuple(nested_effects((program.effects, program.handlers)))
    if not any(COPY_OFFER_FIELD in effect or RESOLVING_COPY_FIELD in effect for effect in instructions):
        return ()
    required = _operand_dependencies(program.effects, program.target_schema)
    if (not required or program.handlers or program.cost_schema is not None or program.event_condition is not None
            or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("A resolving spell copy needs its exact targeted private discard and affected player")
    return required


def is_closed_targeted_discard_copy_program(program):
    try:
        required = targeted_discard_copy_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)
