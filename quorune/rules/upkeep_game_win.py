"""Whole-program authority for one intervening upkeep win."""

from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import freeze_value, thaw_value


PLAYER_WIN_CAPABILITY = "effect.player.game_win"
UPKEEP_GAME_WIN_TEMPLATE = "fixed-intervening-upkeep-game-win-v1"
UPKEEP_GAME_WIN_CAPABILITIES = (
    PLAYER_WIN_CAPABILITY,
    "trigger.condition.fixed_public_state",
    "trigger.placement.apnap",
)

_EXACT_QUANTITY_KINDS = {
    "life": (FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST, FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_MOST),
    "cards in your hand": (FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_LEAST,
                           FixedPublicStateConditionKind.CONTROLLER_HAND_COUNT_AT_MOST),
}


def exact_controller_quantity_win_condition(resource: str, amount: int) -> FixedPublicStateConditionSpec:
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.ALL, schema_version=6,
        terms=tuple(FixedPublicStateConditionSpec(kind, amount=amount) for kind in _EXACT_QUANTITY_KINDS[resource]),
    )


def creature_count_win_condition(zone: str, amount: int) -> FixedPublicStateConditionSpec:
    if zone not in ("battlefield", "graveyard"):
        raise ValueError("Upkeep wins count only the represented current creature sets")
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST, amount=amount, schema_version=2,
        quantity=CharacteristicQuantitySpec(
            CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=(zone,), types_all=("creature",),
                                  token=False if zone == "graveyard" else None),
        ),
    )


def upkeep_game_win_effect() -> dict:
    return {"op": "win_game", "player": "$controller"}


def upkeep_game_win_condition(condition: FixedPublicStateConditionSpec) -> dict:
    return {
        "all": [
            {"all": [
                {"field": "step", "op": "eq", "value": "upkeep"},
                {"field": "player", "op": "eq", "value": "$source.controller"},
            ]},
            {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
             "op": "truthy", "value": True, "condition": condition.to_dict()},
        ],
    }


def _admitted_win_condition(condition: FixedPublicStateConditionSpec) -> bool:
    if condition.kind is FixedPublicStateConditionKind.ALL:
        amount = condition.terms[0].amount
        return bool(amount and any(
            condition == exact_controller_quantity_win_condition(resource, amount)
            for resource in _EXACT_QUANTITY_KINDS
        ))
    if not condition.amount:
        return False
    if condition.kind is FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST:
        return True
    return condition.kind is FixedPublicStateConditionKind.QUERY_COUNT_AT_LEAST and any(
        condition == creature_count_win_condition(zone, condition.amount)
        for zone in ("battlefield", "graveyard")
    )


def upkeep_game_win_runtime_dependencies(program) -> tuple[str, ...]:
    effects = thaw_value(freeze_value(program.effects))
    marked = program.provenance.get("template_id") == UPKEEP_GAME_WIN_TEMPLATE
    if not marked and not any(effect.get("op") == "win_game" for effect in effects):
        return ()
    if (
        not marked or effects != [upkeep_game_win_effect()]
        or (program.event, program.active_zone) != ("step.begin", "battlefield")
        or program.handlers or program.target_schema is not None or program.cost_schema is not None
        or not {"intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE}.issubset(program.coverage)
    ):
        raise ValueError("An upkeep win requires its complete current controller trigger")
    shape = thaw_value(freeze_value(program.event_condition))
    try:
        condition = FixedPublicStateConditionSpec.from_dict(shape["all"][1]["condition"])
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise ValueError("The upkeep win needs its typed intervening condition") from exc
    if not _admitted_win_condition(condition) or shape != upkeep_game_win_condition(condition):
        raise ValueError("The upkeep win condition is outside the admitted live thresholds")
    return UPKEEP_GAME_WIN_CAPABILITIES


def is_closed_upkeep_game_win_program(program) -> bool:
    try:
        required = upkeep_game_win_runtime_dependencies(program)
    except (ValueError, TypeError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
