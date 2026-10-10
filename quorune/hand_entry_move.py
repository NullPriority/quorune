from __future__ import annotations

"""Revalidate one privately chosen incarnation through the existing zone port."""

from .errors import GameRuleError
from .hand_entry_queries import decode_hand_entry_queries, hand_entry_matches
from .object_query import object_query_result
from typing import Any, Mapping


def resolve_selected_hand_entry_card(host, effect: Mapping[str, Any], *, actor: str):
    required = {'op', 'card', 'from', 'destination', 'controller', 'tapped',
        'expected_object_identity', 'hand_entry_query'}
    optional = {'_aura_target_ref', '_replacement_selections', '_runtime_source'}
    if (
        not required <= set(effect) or set(effect) - (required | optional)
        or effect['op'] != 'move' or effect['from'] != 'hand'
        or effect['destination'] != 'battlefield' or effect['controller'] != actor
        or type(effect['tapped']) is not bool
        or not isinstance(effect['expected_object_identity'], str)
        or not effect['expected_object_identity']
        or not isinstance(effect['card'], str) or not effect['card']
    ):
        raise GameRuleError('Selected hand entry has an invalid committed move')
    card = host._resolve_object(actor, effect['card'], zones={'hand'}, owned_only=True)
    if card.logical_object_id != effect['expected_object_identity']:
        raise GameRuleError('The selected hand card changed logical identity')
    try:
        queries = decode_hand_entry_queries(effect['hand_entry_query'])
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    effective = host._effective_card_data(card)
    row = object_query_result(card, effective,
        type_parts=host._type_parts(str(effective.get('type_line') or '')),
        known_to_actor=True, attached_to_ref=None)
    if not hand_entry_matches(row, queries):
        raise GameRuleError('The selected hand card no longer matches its predicate')
    return card
