"""One announced-X draw self-replacement with independently owned results."""

from __future__ import annotations

from collections.abc import Mapping

from ..game_hand_size_model import (
    CAPABILITY as HAND_SIZE_CAPABILITY, no_maximum_for_game_instruction, require_no_maximum_for_game_instruction,
)
from ..graveyard_recycling_model import GRAVEYARD_RECYCLING_CAPABILITY, GRAVEYARD_RECYCLING_OPERATION, GraveyardRecyclingSpec
from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import FrozenMap
from ..resolution_conditions import RESOLUTION_CONDITION_CAPABILITY, RESOLUTION_CONDITION_OPERATION
from ..untargeted_untap_model import FixedLandUntapChoiceSpec, UNTARGETED_UNTAP_CAPABILITY
from .effect_shape_walkers import nested_effects


FIELD = "condition"
_KIND = "announced_x_draw_replacement"
CAPABILITY = "resolution.effect.announced_x_draw_self_replacement"
TEMPLATE = "announced-x-draw-recycling-untap-hand-size-v1"
THRESHOLD = 10
_DEPENDS = (
    CAPABILITY, RESOLUTION_CONDITION_CAPABILITY, DECLARED_AMOUNT_CAPABILITY, GRAVEYARD_RECYCLING_CAPABILITY,
    HAND_SIZE_CAPABILITY, UNTARGETED_UNTAP_CAPABILITY, "permanent.untap.effect",
    "zone.change.destination_replacement", "zone.draw.library_to_hand",
)


def announced_x_draw_replacement_effect(*, player="$controller", x="$x"):
    draw = {"op": "draw", "player": player, "count": x}
    return {
        "op": RESOLUTION_CONDITION_OPERATION, "schema_version": 1,
        FIELD: {"kind": _KIND, "schema_version": 1}, "player": player,
        "cast_fact": x, "minimum": THRESHOLD,
        "effects": [
            {"op": GRAVEYARD_RECYCLING_OPERATION, "player": player, "recycling": GraveyardRecyclingSpec("controller").to_dict()},
            draw, FixedLandUntapChoiceSpec(5).effect(player), no_maximum_for_game_instruction(player),
        ],
        "otherwise_effects": [dict(draw)],
    }


def uses_announced_x_draw_replacement(effect):
    condition = effect.get(FIELD) if isinstance(effect, Mapping) else None
    return isinstance(condition, Mapping) and condition.get("kind") == _KIND


def _validate_effect(effect, *, player="$controller", resolved=False):
    x = effect.get("cast_fact")
    condition = effect.get(FIELD)
    if (not isinstance(condition, Mapping) or type(condition.get("schema_version")) is not int
            or type(effect.get("schema_version")) is not int
            or type(effect.get("minimum")) is not int or type(player) is not str or not player
            or resolved and (player.startswith("$") or type(x) is not int or x < 0)
            or not resolved and x != "$x"
            or FrozenMap(effect) != FrozenMap(announced_x_draw_replacement_effect(player=player, x=x))):
        raise ValueError("The announced-X replacement must keep its exact choice, draw and ordered result")
    if resolved and any(type(child["count"]) is not int for child in (effect["effects"][1], effect["otherwise_effects"][0])):
        raise ValueError("Both draws require the same announced integer X")
    GraveyardRecyclingSpec.from_dict(effect["effects"][0]["recycling"])
    FixedLandUntapChoiceSpec.from_effect(effect["effects"][2])
    require_no_maximum_for_game_instruction(effect["effects"][3], player=player)
    return x


def announced_x_draw_replacement_payload_dependencies(effect):
    if not uses_announced_x_draw_replacement(effect):
        return ()
    _validate_effect(effect)
    return _DEPENDS


def announced_x_draw_replacement_node_capabilities(effects, target_schema, cost_schema):
    if not any(uses_announced_x_draw_replacement(effect) for effect in effects):
        return None
    if len(effects) != 1 or target_schema is not None or cost_schema is not None:
        return ()
    try:
        return announced_x_draw_replacement_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def announced_x_draw_replacement_program_dependencies(program):
    if not any(uses_announced_x_draw_replacement(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = announced_x_draw_replacement_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    if (not required or (program.event, program.active_zone) != ("resolve", "stack")
            or program.event_condition is not None or program.handlers):
        raise ValueError("The announced-X replacement requires its complete untargeted spell")
    return required


def is_closed_announced_x_draw_replacement(program):
    try:
        required = announced_x_draw_replacement_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def bound_announced_x_draw_replacement_branch(effect, actor, stack_controller):
    if actor != stack_controller:
        raise ValueError("The announced-X replacement belongs to the resolving spell's controller")
    x = _validate_effect(effect, player=actor, resolved=True)
    return tuple(FrozenMap(child) for child in effect["effects" if x >= THRESHOLD else "otherwise_effects"])
