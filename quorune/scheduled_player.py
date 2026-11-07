from __future__ import annotations

"""Read-only scheduled-player facts and their sealed departure values."""

from dataclasses import dataclass
from typing import Any, Mapping

from .attachments import attached_object_identity
from .errors import GameRuleError
from .rules.scheduled_player_shapes import (
    SCHEDULED_PLAYER_DEPARTURE_COVERAGE,
    scheduled_player_result_step,
    scheduled_player_subject_instruction,
)


UPKEEP_HAND_COUNT_KIND = 'upkeep_participant_hand_count'
UPKEEP_HAND_COUNT_CAPABILITY = 'trigger.condition.upkeep_participant_hand_count'
UPKEEP_HAND_COUNT_LKI = 'upkeep_participant_hand_lki'


@dataclass(frozen=True, slots=True)
class UpkeepParticipantHandCondition:
    comparison: str
    threshold: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (type(self.comparison) is not str or self.comparison not in ('at_most', 'at_least')
                or type(self.threshold) is not int or self.threshold < 0
                or self.comparison == 'at_least' and self.threshold == 0
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise ValueError('Upkeep hand conditions require a fixed comparison and threshold')

    def to_dict(self) -> dict[str, Any]:
        return {'kind': UPKEEP_HAND_COUNT_KIND, 'schema_version': self.schema_version,
                'comparison': self.comparison, 'threshold': self.threshold}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> UpkeepParticipantHandCondition:
        if (not isinstance(value, Mapping) or set(value) != {'kind', 'schema_version', 'comparison', 'threshold'}
                or value['kind'] != UPKEEP_HAND_COUNT_KIND):
            raise ValueError('Upkeep hand conditions have a closed descriptor')
        return cls(value['comparison'], value['threshold'], value['schema_version'])

    def holds(self, count: int) -> bool:
        return count <= self.threshold if self.comparison == 'at_most' else count >= self.threshold


@dataclass(frozen=True, slots=True)
class UpkeepHandDeparture:
    seat: str
    count: int
    schema_version: int = 1

    def __post_init__(self) -> None:
        if (type(self.seat) is not str or not self.seat or type(self.count) is not int or self.count < 0
                or type(self.schema_version) is not int or self.schema_version != 1):
            raise GameRuleError('Upkeep hand departure requires a seat and public hand count')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version, 'seat': self.seat, 'count': self.count}

    @classmethod
    def from_dict(cls, value: Any) -> UpkeepHandDeparture:
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'seat', 'count'}:
            raise GameRuleError('Upkeep hand departure is missing or malformed')
        return cls(value['seat'], value['count'], value['schema_version'])


def is_upkeep_hand_condition(value: Any) -> bool:
    return isinstance(value, Mapping) and value.get('kind') == UPKEEP_HAND_COUNT_KIND


def _upkeep_participant(state, context: Mapping[str, Any]):
    seat = context.get('player')
    if (context.get('event') not in (None, 'step.begin') or context.get('step') != 'upkeep'
            or state.step != 'upkeep' or type(seat) is not str
            or seat != state.active_player or seat not in state.players):
        raise GameRuleError('Upkeep hand condition lost its scheduled participant')
    return state.players[seat]


def upkeep_hand_condition_holds(state, context: Mapping[str, Any], condition: UpkeepParticipantHandCondition) -> bool:
    participant = _upkeep_participant(state, context)
    if participant.in_game:
        if UPKEEP_HAND_COUNT_LKI in context:
            raise GameRuleError('A live upkeep participant cannot use departure information')
        return condition.holds(len(participant.zones['hand']))
    departure = UpkeepHandDeparture.from_dict(context.get(UPKEEP_HAND_COUNT_LKI))
    if departure.seat != context['player']:
        raise GameRuleError('Upkeep hand departure changed its scheduled participant')
    return condition.holds(departure.count)


def upkeep_hand_departure_context(state, context: Mapping[str, Any]) -> dict[str, Any]:
    participant = _upkeep_participant(state, context)
    if not participant.in_game or UPKEEP_HAND_COUNT_LKI in context:
        raise GameRuleError('Upkeep hand count must be sealed before departure')
    departure = UpkeepHandDeparture(context['player'], len(participant.zones['hand']))
    return {**context, UPKEEP_HAND_COUNT_LKI: departure.to_dict()}


def current_attachment_controller(host,source,condition):
    if set(condition)!={'field','op','value','required_type'} or condition['required_type'] not in {'creature','permanent','artifact','enchantment','land'}:
        raise GameRuleError('Scheduled attachment condition is malformed')
    identity=attached_object_identity(host.state.cards,source)
    if identity is None:return None
    card=host.state.cards[identity.object_id]
    types=host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]
    if condition['required_type']!='permanent' and condition['required_type'] not in types:return None
    return card.controller


def scheduled_player_instruction_is_impossible(host, item, effect) -> bool:
    """Skip only the part of a sealed scheduled result addressed to a departed seat."""
    if not scheduled_player_subject_instruction(effect):
        return False
    program = host.semantics.get(item.semantic_key)
    if program is None or SCHEDULED_PLAYER_DEPARTURE_COVERAGE not in program.coverage:
        return False
    seat = item.context.get('player')
    step = scheduled_player_result_step(program)
    if (item.context.get('event') != 'step.begin' or step is None or item.context.get('step') != step
            or type(seat) is not str or seat not in host.state.players or effect not in program.effects):
        raise GameRuleError('Scheduled result lost its sealed step participant or instruction')
    return not host.state.players[seat].in_game
