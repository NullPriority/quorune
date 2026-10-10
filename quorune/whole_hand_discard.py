from __future__ import annotations

"""Read authoritative hand membership and delegate one simultaneous batch."""

from typing import Any,Protocol
from .errors import GameRuleError
from .whole_hand_discard_model import DiscardWholeHandsIntent


class WholeHandDiscardHost(Protocol):
    state: Any
    active_seats: list[str]
    def apnap_order(self)->list[str]: ...


def resolve_whole_hand_discard(host:WholeHandDiscardHost,intent:DiscardWholeHandsIntent,*,commit_batch):
    snapshot=whole_hand_input_snapshot(host,{'op':'discard_whole_hands','actor':intent.actor,'players':list(intent.players)},actor=intent.actor)
    refs=tuple(member['ref'] for row in snapshot['hands'] for member in row['members'])
    return commit_batch(refs) if refs else ()


def whole_hand_input_snapshot(host,effect,*,actor):
    if effect.get('op')!='discard_whole_hands':
        return None
    raw=effect.get('players')
    if raw=='all':
        players=tuple(host.apnap_order())
    elif raw=='opponents':
        players=tuple(p for p in host.apnap_order() if p!=actor)
    elif isinstance(raw,(list,tuple)) and all(type(p) is str for p in raw):
        players=tuple(raw)
    else:
        raise GameRuleError('Whole-hand discard snapshot players are malformed')
    if effect.get('actor')!=actor or len(players)!=len(set(players)):
        raise GameRuleError('Whole-hand discard snapshot actor changed')
    intent=DiscardWholeHandsIntent(actor=actor,players=players,reason='Whole-hand membership')
    if intent.actor not in host.active_seats or any(p not in host.active_seats for p in intent.players):
        raise GameRuleError('Whole-hand discard player is unavailable')
    refs=[];hands=[]
    for seat in intent.players:
        player=host.state.players[seat]
        members=tuple(player.zones['hand'])
        if len(members)!=len(set(members)):
            raise GameRuleError('Whole-hand discard membership is duplicated')
        rows=[]
        for object_id in members:
            card=host.state.cards.get(object_id)
            if card is None or card.zone!='hand' or card.owner!=seat:
                raise GameRuleError('Whole-hand discard membership is unavailable')
            refs.append(card.ref)
            rows.append({'object_id':card.object_id,'logical_object_id':card.logical_object_id,'ref':card.ref})
        hands.append({'seat':seat,'members':rows})
    if len(refs)!=len(set(refs)):
        raise GameRuleError('Whole-hand discard references are duplicated')
    return {'schema_version':1,'hands':hands}
