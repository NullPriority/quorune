from __future__ import annotations

"""Versioned facts derived from the canonical pre-move zone event, never stored."""

from collections.abc import Mapping, Sequence

ZONE_OBJECT_QUALIFICATIONS_V1 = 'zone_object_qualifications_v1'
_ZONES = frozenset({'library','hand','battlefield','graveyard','stack','exile','command','outside'})


def zone_object_qualifications_v1(event):
    payload = event.payload
    if (event.kind != 'zone.change' or not isinstance(payload, Mapping)
            or ZONE_OBJECT_QUALIFICATIONS_V1 in payload
            or payload.get('object_kind') not in {'card','noncard'} or payload.get('origin') not in _ZONES):
        raise ValueError('Zone object qualification requires canonical pre-move facts')
    types = payload.get('object_types')
    if (not isinstance(types, Sequence) or isinstance(types, (str,bytes))
            or any(type(t) is not str or not t or t != t.casefold() for t in types)):
        raise ValueError('Zone object qualification requires effective pre-move types')
    card = payload['object_kind'] == 'card'
    battlefield = payload['origin'] == 'battlefield'
    affected = event.affected_object
    if (affected is None or type(payload.get('owner')) is not str or not payload['owner']
            or payload['owner'] != affected.owner
            or battlefield and (type(affected.controller) is not str or not affected.controller)):
        raise ValueError('Zone object qualification requires its captured owner and battlefield controller')
    facts = set()
    if card:facts.add('card')
    if battlefield:
        facts.add('permanent')
        if 'creature' in types:facts.add('creature_permanent')
    # CR110.1/111.1: every legal battlefield noncard permanent is a token.
    # CR111.8: a token that left the battlefield cannot move to another zone.
    if card or battlefield:
        facts.add('card_or_token')
    if card and 'creature' in types:
        facts.add('nontoken_creature_permanent' if battlefield else 'creature_card_not_battlefield')
    return frozenset(facts)
