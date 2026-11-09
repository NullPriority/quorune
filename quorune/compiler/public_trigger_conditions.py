from __future__ import annotations

"""Typed intervening conditions over an independently compiled occurrence."""

from dataclasses import replace
import re

from ..continuous_conditions import (
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact,
)
from ..rules.scheduled_player_shapes import SCHEDULED_PLAYER_CAPABILITY, is_upkeep_participant_schedule
from ..scheduled_player import UPKEEP_HAND_COUNT_CAPABILITY, UpkeepParticipantHandCondition
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .public_state_queries import fixed_public_state_condition
from .trigger_event_vocabulary import FixedCounterTriggerEvent


FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY = 'trigger.condition.fixed_public_state'
PublicTriggerCondition = FixedPublicStateConditionSpec | UpkeepParticipantHandCondition
PUBLIC_INTERVENING_IF_TRIGGER = re.compile(
    r'^(?P<prefix>.+?), if (?P<condition>[^,]+), (?P<body>.+)$', re.I,
)
_UPKEEP_HAND_COUNT = re.compile(
    rf'that player has (?P<threshold>{FIXED_COUNT_PATTERN}) or (?P<direction>fewer|more) cards in hand',
    re.I,
)


def upkeep_hand_condition(text: str) -> UpkeepParticipantHandCondition | None:
    if text.casefold() == 'that player has no cards in hand':
        return UpkeepParticipantHandCondition('at_most', 0)
    match = _UPKEEP_HAND_COUNT.fullmatch(text)
    if match is None:
        return None
    comparison = 'at_most' if match['direction'].casefold() == 'fewer' else 'at_least'
    threshold = fixed_number(match['threshold'])
    if threshold == 0 and comparison == 'at_least':
        return None
    return UpkeepParticipantHandCondition(comparison, threshold)


def bind_public_intervening_trigger(match, *, card_name, compile_binding, compile_source_binding):
    condition = upkeep_hand_condition(match['condition']) or fixed_public_state_condition(
        match['condition'], source_name=card_name or 'This source',
    )
    if condition is None:
        return None
    if isinstance(condition, FixedPublicStateConditionSpec) and any(leaf.kind in {
        FixedPublicStateConditionKind.SOURCE_ENTERED_THIS_TURN,
        FixedPublicStateConditionKind.SOURCE_COUNTER_AT_LEAST,
        FixedPublicStateConditionKind.ATTACHED_MATCHES_QUERY,
    } for leaf in condition.leaf_conditions()):
        return None
    body = match["body"]
    if (match["condition"].casefold() == "this permanent is an enchantment"
            and body.casefold().startswith("it becomes a ")):
        body = "This permanent " + body[3:]
    if (
        isinstance(condition, FixedPublicStateConditionSpec)
        and condition.fact is FixedPublicStateFact.CONTROLLER_BELOW_STARTING_LIFE
        and re.fullmatch(r"it becomes equal to your starting life total\.?", body, re.I)
    ):
        body = "Your life total becomes equal to your starting life total."
    text = f"{match['prefix']}, {body}"
    base = compile_binding(text, card_name=card_name) or compile_source_binding(text, card_name=card_name)
    if base is None or base.public_state_condition is not None:
        return None
    if isinstance(condition, UpkeepParticipantHandCondition) and (
        base.active_zone != 'battlefield' or base.event is not FixedCounterTriggerEvent.STEP_BEGIN
        or not is_upkeep_participant_schedule(base.event_condition)
    ):
        return None
    return replace(base, public_state_condition=condition)


def public_trigger_condition_capabilities(condition: PublicTriggerCondition | None) -> tuple[str, ...]:
    if condition is None:
        return ()
    if isinstance(condition, UpkeepParticipantHandCondition):
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, SCHEDULED_PLAYER_CAPABILITY, UPKEEP_HAND_COUNT_CAPABILITY)
    if condition.kind is FixedPublicStateConditionKind.CONTROLLER_CREATURE_NUMERIC_EXISTS:
        return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY, 'condition.creature.fixed_numeric_exists')
    return (FIXED_PUBLIC_STATE_INTERVENING_CAPABILITY,)
