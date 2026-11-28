from __future__ import annotations

"""Closed controller d20 draw and subsequent game-duration cleanup grant."""

from collections.abc import Mapping
from typing import Any

from ..game_hand_size_model import (
    CAPABILITY as HAND_SIZE_CAPABILITY,
    OPERATION as HAND_SIZE_OPERATION,
    no_maximum_for_game_instruction,
    require_no_maximum_for_game_instruction,
)
from ..replacement.immutable import FrozenMap
from .random_selection import publish_resolution_die_roll

MECHANIC = "single-d20-draw-game-hand-size"
CAPABILITY = "random.die.single_d20_draw"
ROLL_FIELD = "single_d20_draw_version"
SIDES = 20
_EVENT = "damage.dealt.self"
_CONDITION = FrozenMap({"all": [
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
    {"field": "source_logical_object_id", "op": "eq", "value": "$source.logical_object_id"},
]})
_BODY_CAPABILITIES = (CAPABILITY, HAND_SIZE_CAPABILITY, "zone.draw.library_to_hand")
_EVENT_CAPABILITIES = ("trigger.effect.fixed_event", "trigger.event.normalized_damage", "trigger.placement.apnap")


def _draw_instruction() -> dict[str, Any]:
    return {"op": "draw", "player": "$controller", ROLL_FIELD: 1}


def die_draw_hand_size_instructions() -> tuple[dict[str, Any], ...]:
    return (_draw_instruction(), no_maximum_for_game_instruction())


def _require_die_draw(effect: Mapping[str, Any]) -> None:
    if type(effect.get(ROLL_FIELD)) is not int or effect != _draw_instruction():
        raise ValueError("A single d20 draw requires its complete resolving-player instruction")


def declares_die_draw_hand_size(value: Any) -> bool:
    if isinstance(value, Mapping):
        return (ROLL_FIELD in value or value.get("op") == HAND_SIZE_OPERATION
                or any(declares_die_draw_hand_size(child) for child in value.values()))
    return isinstance(value, (tuple, list)) and any(declares_die_draw_hand_size(child) for child in value)


HAND_COUNT_TEMPLATE = "controller-hand-count-draw-game-hand-size-v1"


def controller_hand_count_draw_and_size_instructions():
    from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
    from ..object_predicate import ObjectQuerySpec
    from ..query_effect_amount_model import PublicQueryAmountSpec

    hand = CharacteristicQuantitySpec(
        CharacteristicQuantityScope.CONTROLLER_ZONE, query=ObjectQuerySpec(zones=("hand",)),
        schema_version=2, offset=1,
    )
    draw = {"op": "draw", "player": "$controller", "count": PublicQueryAmountSpec(hand).to_dict()}
    return (draw, no_maximum_for_game_instruction())


def controller_hand_count_draw_and_size_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    from ..query_effect_amount_model import PublicQueryAmountSpec

    if ("public-query-effect-amount" not in mechanics or target_schema is not None
            or cost_schema is not None or len(effects) != 2):
        return ()
    draw, grant = effects
    if set(draw) != {"op", "player", "count"} or draw["op"] != "draw" or draw["player"] != "$controller":
        return ()
    try:
        amount = PublicQueryAmountSpec.from_dict(draw["count"])
        require_no_maximum_for_game_instruction(grant, player="$controller")
    except (TypeError, ValueError, KeyError):
        return ()
    wanted, _grant = controller_hand_count_draw_and_size_instructions()
    if amount.to_dict() != wanted["count"]:
        return ()
    return ("quantity_expression.public_query_effect_amount", "zone.draw.library_to_hand", HAND_SIZE_CAPABILITY)


def is_closed_controller_hand_count_draw_and_size_program(program) -> bool:
    if program.provenance.get("template_id") != HAND_COUNT_TEMPLATE:
        return False
    try:
        dependencies = die_draw_hand_size_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(dependencies) and set(dependencies) <= set(program.capability_dependencies)


def declared_die_draw_node_capabilities(effects, target_schema, mechanics, cost_schema):
    """Distinguish an absent dice shape from a declared shape that fails closed."""
    from .announced_x_draw_replacement import uses_announced_x_draw_replacement
    from .equipped_attack_die import equipped_attack_die_declared, equipped_attack_die_node_capabilities

    if any(uses_announced_x_draw_replacement(effect) for effect in effects):
        return None
    if equipped_attack_die_declared(effects):
        return equipped_attack_die_node_capabilities(
            effects=effects, target_schema=target_schema, mechanics=mechanics, cost_schema=cost_schema,
        )
    hand_count = controller_hand_count_draw_and_size_node_capabilities(
        effects=effects, target_schema=target_schema, mechanics=mechanics, cost_schema=cost_schema,
    )
    if hand_count:
        return hand_count
    if MECHANIC not in mechanics and not declares_die_draw_hand_size(effects):
        return None
    return die_draw_hand_size_node_capabilities(
        effects=effects, target_schema=target_schema, mechanics=mechanics, cost_schema=cost_schema,
    )


def die_draw_hand_size_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if MECHANIC not in mechanics or target_schema is not None or cost_schema is not None or len(effects) != 2:
        return ()
    try:
        _require_die_draw(effects[0])
        require_no_maximum_for_game_instruction(effects[1], player="$controller")
    except ValueError:
        return ()
    return _BODY_CAPABILITIES


def die_draw_hand_size_runtime_dependencies(program):
    from .announced_x_draw_replacement import (
        announced_x_draw_replacement_program_dependencies, uses_announced_x_draw_replacement,
    )

    if any(uses_announced_x_draw_replacement(effect) for effect in program.effects):
        return announced_x_draw_replacement_program_dependencies(program)
    if program.provenance.get("template_id") == HAND_COUNT_TEMPLATE:
        dependencies = controller_hand_count_draw_and_size_node_capabilities(
            effects=program.effects, target_schema=program.target_schema,
            mechanics=program.coverage, cost_schema=program.cost_schema,
        )
        if not dependencies or program.handlers or (program.event, program.active_zone) != ("resolve", "stack"):
            raise ValueError("A hand-count draw followed by a game-duration hand-size grant requires one resolving spell")
        return dependencies
    if not (MECHANIC in program.coverage or declares_die_draw_hand_size(
        (program.effects, program.handlers, program.target_schema, program.cost_schema, program.event_condition),
    )):
        return ()
    dependencies = die_draw_hand_size_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        cost_schema=program.cost_schema,
    )
    if (not dependencies or program.handlers or (program.event, program.active_zone) != (_EVENT, "battlefield")
            or not isinstance(program.event_condition, Mapping)
            or FrozenMap(program.event_condition) != _CONDITION):
        raise ValueError("The d20 draw and game-duration hand-size grant require exactly one source combat-to-player trigger")
    return (*dependencies, *_EVENT_CAPABILITIES)


def bind_die_draw(host, item, effect, *, instruction_pointer: int) -> dict[str, Any]:
    """Roll once immediately before starting the ordinary draw continuation."""
    _require_die_draw(effect)
    if (item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or item.context.get("event") != "damage.dealt" or item.context.get("combat") is not True
            or item.context.get("target_kind") != "player"
            or not item.context.get("source_logical_object_id") or host.state.continuous_effects is None):
        raise ValueError("A single d20 draw requires its resolving source combat occurrence")
    controller = item.controller
    host._require_seat(controller, in_game=True)
    result = publish_resolution_die_roll(
        host, controller=controller, stack_ref=item.ref, instruction_pointer=instruction_pointer, sides=SIDES,
    )
    return {"op": "draw", "player": controller, "count": result}
