from __future__ import annotations

"""Capability closure for fixed life and ordered controller effects."""

from typing import Any, Iterable, Mapping, Sequence

from .node_capability_shapes import (
    fixed_counter_placement_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_scry_node_capabilities,
)
from .affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities,
    fixed_controller_discard_node_capabilities,
)
from ..compiler.affected_player_discard_templates import FIXED_CONTROLLER_DISCARD_MECHANIC
from ..target_history import PLAYER_HISTORY_FIELD, TargetPlayerHistoryKind, TargetPlayerHistorySpec


_FIXED_CONTROLLER_SEQUENCE_MECHANIC = "fixed-controller-effect-sequence"
_FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC = (
    "fixed-counter-controller-effect-sequence"
)
_LIFE_OPERATION = "life"


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def player_history_life_loss_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (
        len(effects) != 1 or effects[0] != {"op": "lose_life", "player": "$target.0", "amount": 1}
        or type(effects[0].get("amount")) is not int or not isinstance(target_schema, Mapping)
        or type(target_schema.get("count")) is not int
    ):
        return ()
    try:
        history = TargetPlayerHistorySpec.from_mapping(target_schema.get(PLAYER_HISTORY_FIELD))
    except (TypeError, ValueError):
        return ()
    ordinary = {k: v for k, v in target_schema.items() if k != PLAYER_HISTORY_FIELD}
    if ordinary != {"zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1}:
        return ()
    base = fixed_life_node_capabilities(effects=effects, target_schema=ordinary, mechanic_ids=mechanic_ids)
    if not base:
        return ()
    historical_owner = (
        "activation.condition.current_turn_events" if history.kind is TargetPlayerHistoryKind.LOST_LIFE
        else "damage.source.stack_current_or_lki"
    )
    return (*base, historical_owner)


def player_history_life_loss_runtime_dependencies(program):
    from ..compiler.life_templates import PLAYER_HISTORY_LIFE_LOSS_TEMPLATE
    from .effect_shape_walkers import contains_nested_key

    provenance = getattr(program, "provenance", None)
    template = provenance.get("template_id") if isinstance(provenance, Mapping) else None
    if (template != PLAYER_HISTORY_LIFE_LOSS_TEMPLATE
            and not contains_nested_key(program.target_schema, PLAYER_HISTORY_FIELD)):
        return ()
    if (program.event, program.active_zone) != ("activate", "battlefield") or any(
        not isinstance(handler, Mapping) or handler.get("handler_id") != "activation.catalog.pinned.v1"
        for handler in program.handlers
    ):
        raise ValueError("Player history requires one ordinary battlefield activation")
    owned = player_history_life_loss_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not owned:
        raise ValueError("Player history life loss lacks its exact shared target and result")
    return owned


def temporary_life_gain_runtime_dependencies(program):
    from ..life_gain_permission import (
        LIFE_GAIN_PROHIBITION_CAPABILITY, TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD,
        validate_temporary_life_gain_effect,
    )
    from .effect_shape_walkers import nested_effects

    effects = (*program.effects, *getattr(program, "handlers", ()))
    if not any(TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD in effect for effect in nested_effects(effects)):
        return ()
    if len(program.effects) == 3 and TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD in program.effects[0]:
        from .damage_capability_shapes import turn_damage_prevention_runtime_dependencies

        required = turn_damage_prevention_runtime_dependencies(program)
        if required:
            return (LIFE_GAIN_PROHIBITION_CAPABILITY, "life.change.effect")
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    handlers = tuple(getattr(program, "handlers", ()))
    ordinary_activation = (
        program.event == "activate" and len(handlers) == 1
        and handlers[0].get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID
    )
    if (
        (program.event, program.active_zone) not in {("activate", "battlefield"), ("resolve", "stack")}
        or program.target_schema is not None or len(program.effects) != 1
        or handlers and not ordinary_activation
    ):
        raise ValueError("Temporary life permission requires one complete untargeted resolution")
    spec = validate_temporary_life_gain_effect(program.effects[0])
    if spec.affected_player_relation != "opponent":
        raise ValueError("Standalone life prohibition requires its exact opponent relation")
    return (LIFE_GAIN_PROHIBITION_CAPABILITY, "life.change.effect")


def fixed_life_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return life ownership only for the closed fixed-value grammar."""

    if isinstance(target_schema, Mapping) and PLAYER_HISTORY_FIELD in target_schema:
        return player_history_life_loss_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-119-life" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    operation = effect.get("op")
    from ..life_gain_permission import (
        LIFE_GAIN_PROHIBITION_CAPABILITY, TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD,
        validate_temporary_life_gain_effect,
    )

    if TEMPORARY_LIFE_GAIN_PROHIBITION_FIELD in effect:
        if target_schema is not None:
            return ()
        try:
            spec = validate_temporary_life_gain_effect(effect)
        except (ValueError, TypeError):
            return ()
        if spec.affected_player_relation != "opponent":
            return ()
        return (LIFE_GAIN_PROHIBITION_CAPABILITY, "life.change.effect")
    if 'public_life_gain_version' in effect:
        from .public_life_gain_scopes import public_life_gain_node_capabilities
        return public_life_gain_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    from .characteristic_life_exchange import CHARACTERISTIC_LIFE_EXCHANGE_OPERATION, exchange_instruction_dependencies

    from .player_life_exchange import PLAYER_LIFE_EXCHANGE_OPERATION, player_life_exchange_instruction_dependencies

    exchange_owners = {
        CHARACTERISTIC_LIFE_EXCHANGE_OPERATION: exchange_instruction_dependencies,
        PLAYER_LIFE_EXCHANGE_OPERATION: player_life_exchange_instruction_dependencies,
    }
    if resolve_exchange := exchange_owners.get(operation):
        try:
            return resolve_exchange(effects, target_schema)
        except (ValueError, TypeError):
            return ()
    if operation=='set_life_total':
        from .life_total_setting_shapes import life_total_setting_capabilities
        return life_total_setting_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    if operation=='life_loss_then_gain_actual':
        from .actual_life_transfer import actual_life_transfer_capabilities
        return actual_life_transfer_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
    dependencies = {"life.change.effect"}
    any_player_schema = {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": "any",
        "count": 1,
    }
    opponent_schema = {
        **any_player_schema,
        "player_relation": "opponent",
    }
    actual_schema = dict(target_schema or {})
    if operation == _LIFE_OPERATION:
        player = effect.get("player")
        valid = (
            set(effect) == {"op", "player", "delta"}
            and isinstance(player,str) and player in {"$controller", "$target.0"}
            and _positive_int(effect.get("delta"))
            and (
                player == "$controller"
                or actual_schema in (any_player_schema, opponent_schema)
            )
        )
        expected_schema = (
            None if player == "$controller" else actual_schema
        )
    elif operation == "lose_life":
        player = effect.get("player")
        all_players = (
            set(effect) == {"op", "scope", "amount"}
            and effect.get("scope") == "all_active_players"
            and _positive_int(effect.get("amount"))
            and "cr-101-the-magic-golden-rules" in mechanics
            and target_schema is None
        )
        valid = all_players or (
            set(effect) == {"op", "player", "amount"}
            and isinstance(player,str) and player in {"$controller", "$target.0"}
            and _positive_int(effect.get("amount"))
            and (
                player == "$controller"
                or actual_schema in (any_player_schema, opponent_schema)
            )
        )
        expected_schema = (
            None if all_players or player == "$controller" else actual_schema
        )
    elif operation == "lose_life_each_opponent":
        valid = (
            set(effect) == {"op", "amount"}
            and _positive_int(effect.get("amount"))
            and "cr-101-the-magic-golden-rules" in mechanics
        )
        expected_schema = None
    elif operation == "drain_opponent":
        valid = (
            set(effect) == {"op", "target", "amount"}
            and effect.get("target") == "$target.0"
            and _positive_int(effect.get("amount"))
        )
        expected_schema = opponent_schema
    elif operation == "drain_each_opponent":
        valid = (
            set(effect) == {"op", "amount"}
            and _positive_int(effect.get("amount"))
            and "cr-101-the-magic-golden-rules" in mechanics
        )
        expected_schema = None
    else:
        valid = False
        expected_schema = None
    if not valid or dict(target_schema or {}) != dict(expected_schema or {}):
        return ()
    if target_schema is not None:
        if "cr-115-targets" not in mechanics:
            return ()
        dependencies.add("target.revalidate_resolution")
    return tuple(sorted(dependencies))


def fixed_controller_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own exactly two ordered controller effects containing one draw."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        _FIXED_CONTROLLER_SEQUENCE_MECHANIC not in mechanics
        or target_schema is not None
        or len(effects) != 2
    ):
        return ()
    dependencies: set[str] = {"resolution.effect_sequence.fixed_controller"}
    draw_count = 0
    for effect in effects:
        operation = effect.get("op")
        if operation == "draw":
            required = fixed_draw_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cr-121-drawing-a-card",),
            )
            draw_count += 1
        elif operation == "choose_cards_apnap":
            if effect.get("players") != ["$controller"]:
                return ()
            required = fixed_controller_discard_node_capabilities(
                effects=(effect,), target_schema=None, mechanic_ids=mechanics,
            ) if FIXED_CONTROLLER_DISCARD_MECHANIC in mechanics else fixed_affected_player_discard_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=(
                    "fixed-affected-player-discard",
                    "cr-402-hand",
                ),
                allow_controller=True,
            )
        elif operation in {_LIFE_OPERATION, "lose_life"}:
            required = fixed_life_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("cr-119-life",),
            )
        elif operation == "scry":
            required = fixed_scry_node_capabilities(
                effects=(effect,),
                target_schema=None,
                mechanic_ids=("scry",),
            )
        elif operation == "discard_random_cards":
            from ..random_discard import random_discard_node_capabilities
            required = random_discard_node_capabilities(
                effects=(effect,), target_schema=None, mechanic_ids=('fixed-random-discard',))
        else:
            return ()
        if not required:
            return ()
        dependencies.update(required)
    if draw_count != 1:
        return ()
    return tuple(sorted(dependencies))


def fixed_counter_controller_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own one fixed counter placement and one fixed controller effect."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        _FIXED_COUNTER_CONTROLLER_SEQUENCE_MECHANIC not in mechanics
        or len(effects) != 2
    ):
        return ()
    counter_effects = tuple(
        effect for effect in effects if effect.get("op") == "place_counters"
    )
    controller_effects = tuple(
        effect for effect in effects if effect.get("op") != "place_counters"
    )
    if len(counter_effects) != 1 or len(controller_effects) != 1:
        return ()
    counter_effect = counter_effects[0]
    if counter_effect.get("card") == "$source.zone_object":
        if target_schema is not None:
            return ()
        normalized_counter = {**counter_effect, "card": "$source"}
    elif counter_effect.get("card") == "$target.0":
        normalized_counter = counter_effect
    else:
        return ()
    counter_dependencies = fixed_counter_placement_node_capabilities(
        effects=(normalized_counter,),
        target_schema=target_schema,
        mechanic_ids=mechanics,
    )
    if not counter_dependencies:
        return ()
    controller_effect = controller_effects[0]
    operation = controller_effect.get("op")
    if operation == "draw":
        controller_dependencies = fixed_draw_node_capabilities(
            effects=(controller_effect,),
            target_schema=None,
            mechanic_ids=("cr-121-drawing-a-card",),
        )
    elif operation in {_LIFE_OPERATION, "lose_life"}:
        controller_dependencies = fixed_life_node_capabilities(
            effects=(controller_effect,),
            target_schema=None,
            mechanic_ids=("cr-119-life",),
        )
    elif operation == "scry":
        controller_dependencies = fixed_scry_node_capabilities(
            effects=(controller_effect,),
            target_schema=None,
            mechanic_ids=("scry",),
        )
    else:
        return ()
    if not controller_dependencies:
        return ()
    return tuple(
        sorted(
            {
                "resolution.effect_sequence.fixed_counter_controller",
                *counter_dependencies,
                *controller_dependencies,
            }
        )
    )


__all__ = [
    "fixed_counter_controller_effect_sequence_node_capabilities",
    "fixed_controller_effect_sequence_node_capabilities",
    "fixed_life_node_capabilities",
]
