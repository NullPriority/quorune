from __future__ import annotations

"""Read-only current recipient controller for a scheduled Aura trigger."""

from .attachments import attached_object_identity
from .errors import GameRuleError
from .rules.scheduled_player_shapes import (
    SCHEDULED_PLAYER_DEPARTURE_COVERAGE,
    scheduled_player_result_step,
    scheduled_player_subject_instruction,
)


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
