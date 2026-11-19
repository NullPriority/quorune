from __future__ import annotations

"""Enumerate and commit one currently legal hand spell under a fixed free price."""

from ..bounded_hand_cast_model import BoundedHandCastSpec
from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..public_quantity_reductions import public_mana_value
from ..replacement.immutable import thaw_value
from .casting.face_queries import offered_cast_methods, prospective_cast_card
from .casting.characteristic_methods import characteristic_cast_view
from .casting.proposal import build_cast_offer
from .resolving_cast_payments import commit_resolving_cast


def _live_item(host, actor, stack_ref):
    item = next((row for row in host.state.stack if row.ref == stack_ref), None)
    if (item is None or item.controller != actor or actor not in host.active_seats
            or not item.context.get('currently_resolving')):
        raise GameRuleError('The hand free cast lost its original resolution')
    return item


def _permission(effect, actor):
    instruction = {key: value for key, value in effect.items() if key != '_options_fingerprint'}
    return BoundedHandCastSpec.from_effect(instruction, actor=actor)


def _option_pairs(host, actor, spec):
    pairs = []
    player = host.state.players[actor]
    for object_id in player.zones['hand']:
        card = host.state.cards[object_id]
        if card.zone != 'hand' or card.owner != actor:
            raise GameRuleError('The hand free cast contains an invalid owned hand object')
        for method, face in offered_cast_methods(host, card):
            if method not in (None, 'fuse'):
                continue
            result = build_cast_offer(host, actor, card, cast_method=method, face_name=face,
                                      resolving_payment='free', resolving_origin='hand')
            if result.offer is None:
                continue
            view, _record = characteristic_cast_view(host, card, host.card_record(card), method)
            view = prospective_cast_card(host, view, face)
            if not spec.mana_value_limit.permits(public_mana_value(host._effective_card_data(view))):
                continue
            offer = result.offer.to_dict(include_fingerprint=False)
            for price in offer['cost_options']:
                canonical = {**price, 'face': offer.get('face'), 'cast_method': method}
                fingerprint = discover_options_fingerprint(({'logical_object_id': card.logical_object_id,
                                                             'price': canonical},))
                public = {**canonical, 'id': f'bounded-hand:{card.ref}:{fingerprint}', 'card': card.ref,
                          'label': f"{card.printed_name} — {price.get('label', offer['label'])}"}
                pairs.append((public, canonical, card))
    return tuple(pairs)


def bounded_hand_cast_options(host, *, item, actor, effect):
    if item is None:
        raise GameRuleError('A hand free cast requires its resolving stack item')
    _live_item(host, actor, item.ref)
    try:
        spec = _permission(effect, actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return tuple(public for public, _, _ in _option_pairs(host, actor, spec))


def execute_bounded_hand_cast(host, intent):
    item = _live_item(host, intent.actor, intent.stack_ref)
    pairs = _option_pairs(host, intent.actor, intent.spec)
    if discover_options_fingerprint(tuple(public for public, _, _ in pairs)) != intent.options_fingerprint:
        raise GameRuleError('The hand free cast choices changed before completion')
    if intent.response is None:
        return
    response = thaw_value(intent.response)
    option_id = response.get('cost_option')
    matches = [(option, card) for public, option, card in pairs
               if option_id in (None, public['id']) and response.get('card', card.ref) == card.ref
               and response.get('face') in (None, option['face'])
               and response.get('cast_method') in (None, option['cast_method'])]
    if len(matches) != 1 or response.get('from', 'hand') != 'hand':
        raise GameRuleError('Select one currently issued hand free cast option')
    option, card = matches[0]
    original = card.logical_object_id
    response.update(card=card.ref, cost_option=option['id'], auto_pay=True)
    response['from'] = 'hand'
    for field in ('face', 'cast_method'):
        if option[field] is not None:
            response[field] = option[field]
    commit_resolving_cast(host, actor=intent.actor, response=response, origin='hand',
        payment_id=f'{item.ref}:bounded-hand:{original}', replacement_selections=intent.replacement_selections)
    current = host.state.cards[card.object_id]
    if (current.zone != 'stack' or current.controller != intent.actor or current.logical_object_id == original
            or not any(row.kind == 'spell' and row.card_object_id == current.object_id for row in host.state.stack)):
        raise StateInvariantError('The selected hand card created no spell')
