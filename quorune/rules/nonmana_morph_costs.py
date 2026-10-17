from __future__ import annotations

"""Current-resource queries and canonical payment for fixed nonmana Morph."""

from ..additional_cost_vocabulary import DISCARD_ONE_COST, RETURN_ONE_TO_OWNER_HAND_COST
from ..errors import GameRuleError
from ..life_state import LifeStateError, prepare_life_payment, pay_life_cost
from ..nonmana_morph_model import FixedNonmanaMorphSpec, MorphObjectCostKind
from ..object_predicate import ObjectQuerySpec
from .casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


def morph_zone_cost(spec):
    if not isinstance(spec, FixedNonmanaMorphSpec):
        return None
    cost = spec.nonmana_cost
    if cost.kind not in {MorphObjectCostKind.DISCARD, MorphObjectCostKind.RETURN}:
        return None
    discard = cost.kind is MorphObjectCostKind.DISCARD
    return FixedZoneChangeAdditionalCost(
        operation=DISCARD_ONE_COST if discard else RETURN_ONE_TO_OWNER_HAND_COST,
        choice_field='discard_cards' if discard else 'return_cards', count=cost.count,
        predicate=ObjectQuerySpec(zones=('hand' if discard else 'battlefield',),
            owner='$actor' if discard else None, controller=None if discard else '$actor',
            subtypes_all=(cost.subtype,) if cost.subtype else (), known_to_actor=True))


def _reveal_candidates(host, seat, color):
    return tuple(card.ref for oid in host.state.players[seat].zones['hand']
        if (card := host.state.cards[oid]).owner == seat
        and color in (host._effective_card_data(card).get('colors') or ()))


def nonmana_morph_offer_fields(host, seat, spec):
    """None means unaffordable; candidate identities stay in the actor's offer."""
    if not isinstance(spec, FixedNonmanaMorphSpec):
        return {}
    cost = spec.nonmana_cost
    if cost.kind is MorphObjectCostKind.LIFE:
        try:
            prepare_life_payment(host, seat, cost.count)
        except LifeStateError:
            return None
        return {}
    zone_cost = morph_zone_cost(spec)
    refs = (fixed_zone_change_cost_candidates(host, actor=seat, cost=zone_cost)
        if zone_cost else _reveal_candidates(host, seat, cost.color))
    if len(refs) < cost.count:
        return None
    field = zone_cost.choice_field if zone_cost else 'reveal_cards'
    return {'choice_schema': {field: {'type': 'array', 'min_items': cost.count,
        'max_items': cost.count, 'items': {'enum': list(refs)}}}}


def pay_nonmana_morph_cost(host, seat, source, spec, response):
    if not isinstance(spec, FixedNonmanaMorphSpec):
        return
    cost = spec.nonmana_cost
    if cost.kind is MorphObjectCostKind.LIFE:
        try:
            pay_life_cost(host, seat, cost.count)
        except LifeStateError as exc:
            raise GameRuleError(str(exc)) from exc
        return
    zone_cost = morph_zone_cost(spec)
    if zone_cost is not None:
        from .activation_costs import pay_fixed_zone_change_cost
        pay_fixed_zone_change_cost(host, actor=seat, cost=zone_cost,
            refs=response.get(zone_cost.choice_field), response=response, reason='Morph turn-face-up cost')
        return
    raw = response.get('reveal_cards')
    if (not isinstance(raw, (tuple, list)) or len(raw) != 1 or type(raw[0]) is not str
            or raw[0] not in _reveal_candidates(host, seat, cost.color)):
        raise GameRuleError('Morph requires one currently eligible colored hand card')
    card = host._resolve_object(seat, raw[0], zones={'hand'}, owned_only=True)
    selection = {'object_id': card.object_id, 'logical_object_id': card.logical_object_id,
        'ref': card.ref, 'player': seat, 'oracle_id': card.oracle_id, 'name': card.printed_name}
    from ..effect_runtime.hand_inspection import validate_entry_hand_reveal, commit_selected_hand_reveal
    validate_entry_hand_reveal(host, selection)
    commit_selected_hand_reveal(host, selection, source_ref=source.ref,
        summary='A card was revealed to pay a Morph turn-face-up cost.')
