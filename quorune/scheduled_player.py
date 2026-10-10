from __future__ import annotations

"""Read-only current recipient controller for a scheduled Aura trigger."""

from .attachments import attached_object_identity
from .errors import GameRuleError


def current_attachment_controller(host,source,condition):
    if set(condition)!={'field','op','value','required_type'} or condition['required_type'] not in {'creature','permanent','artifact','enchantment','land'}:
        raise GameRuleError('Scheduled attachment condition is malformed')
    identity=attached_object_identity(host.state.cards,source)
    if identity is None:return None
    card=host.state.cards[identity.object_id]
    types=host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0]
    if condition['required_type']!='permanent' and condition['required_type'] not in types:return None
    return card.controller
