from __future__ import annotations

"""Behold selection uses one current query; disclosure stays with its existing owner."""

from collections.abc import Mapping
from ...object_predicate import ObjectQuerySpec
from ...object_query import object_query_result, object_matches_query
from ...effect_runtime.hand_inspection import validate_entry_hand_reveal, begin_stack_cost_hand_reveal
from ..behold_additional_cost import BEHOLD_ADDITIONAL_COST_KIND, fixed_behold_additional_cost
from ...linked_exile_return_model import ExiledObjectIdentity
from .model import CastProposalError


def _matches(host, seat, card, cost):
    if card.zone not in {'hand', 'battlefield'}:
        return False
    if card.zone == 'hand' and card.object_id not in host.state.players[seat].zones['hand']:
        return False
    query = ObjectQuerySpec(zones=(card.zone,), subtypes_all=(cost.subtype,),
        **({'owner': seat, 'token': False} if card.zone == 'hand' else {'controller': seat}))
    effective = host._effective_card_data(card)
    row = object_query_result(card, effective, type_parts=host._type_parts(str(effective.get('type_line') or '')),
        known_to_actor=True, attached_to_ref=None)
    return object_matches_query(row, query)


def behold_cost_candidates(host, seat, source, cost):
    hand = [host.state.cards[oid] for oid in host.state.players[seat].zones['hand']]
    battlefield = sorted((card for card in host.state.cards.values() if card.zone == 'battlefield'),
        key=lambda card: (card.zone_timestamp, card.object_id))
    return tuple(card for card in (*hand, *battlefield)
        if card.object_id != source.object_id and _matches(host, seat, card, cost))


def behold_cost_selection(host, *, seat, source, cost, response, hint, choice_schema, cost_position):
    cards = behold_cost_candidates(host, seat, source, cost)
    if not cards:
        return False, None
    refs = [card.ref for card in cards]
    label = (f'Behold and exile a matching {cost.subtype}' if cost.linked_exile_pair
             else f'Behold a matching {cost.subtype}: choose a permanent or reveal a hand card')
    choice_schema['behold_cards'] = {'type': 'object_ref_array', 'legal_refs': refs,
        'minimum': 1, 'maximum': 1, 'distinct': True, 'payment': 'behold',
        'label': label}
    if hint:
        return True, None
    selected = response.get('behold_cards')
    if type(selected) is not list or len(selected) != 1 or type(selected[0]) is not str or selected[0] not in refs:
        return False, None
    card = next(card for card in cards if card.ref == selected[0])
    identity = {'object_id': card.object_id, 'logical_object_id': card.logical_object_id,
        'ref': card.ref, 'player': seat, 'zone': card.zone, 'reveal': None}
    if card.zone == 'hand':
        identity['reveal'] = {key: identity[key] for key in ('object_id', 'logical_object_id', 'ref', 'player')}
        identity['reveal'].update(oracle_id=card.oracle_id, name=card.printed_name)
    return True, {'schema_version': 1, 'kind': BEHOLD_ADDITIONAL_COST_KIND,
        'cost_position': cost_position, 'selection': identity}


def _validate_selection(host, proposal, source, selected, cost):
    if (not isinstance(selected, Mapping) or set(selected) != {'schema_version', 'kind', 'cost_position', 'selection'}
            or type(selected['schema_version']) is not int or selected['schema_version'] != 1
            or type(selected['cost_position']) is not int):
        raise CastProposalError('Behold casting selection is malformed')
    identity = selected['selection']
    fields = {'object_id', 'logical_object_id', 'ref', 'player', 'zone', 'reveal'}
    if (not isinstance(identity, Mapping) or set(identity) != fields
            or any(type(identity[key]) is not str or not identity[key] for key in fields - {'reveal'})
            or identity['zone'] not in {'hand', 'battlefield'} or identity['player'] != proposal.seat):
        raise CastProposalError('Behold casting identity is malformed')
    card = host.state.cards.get(identity['object_id'])
    if (card is None or card.object_id == source.object_id or card.logical_object_id != identity['logical_object_id']
            or card.ref != identity['ref'] or card.zone != identity['zone'] or not _matches(host, proposal.seat, card, cost)):
        raise CastProposalError('Behold casting selection lost its current quality, control or incarnation')
    if card.zone == 'hand':
        revealed = validate_entry_hand_reveal(host, identity['reveal'])
        if revealed is not card or any(identity['reveal'][key] != identity[key]
                for key in ('object_id', 'logical_object_id', 'ref', 'player')):
            raise CastProposalError('Behold reveal does not identify its selected hand card')
    elif identity['reveal'] is not None:
        raise CastProposalError('Choosing a permanent cannot disclose a hand card')
    return card


def _selected_behold_cost(selected, selected_option):
    costs = selected_option.get('additional_costs', ())
    position = selected.get('cost_position')
    if type(position) is not int or not 0 <= position < len(costs):
        raise CastProposalError('Behold casting cost position changed')
    cost = fixed_behold_additional_cost(costs[position])
    if cost is None:
        raise CastProposalError('Behold casting cost disappeared')
    return cost


def commit_linked_behold_cost(host, proposal, source, result, selected, selected_option, response):
    from copy import deepcopy
    from .zone_cost_commit import commit_card_cost_changes

    cost = _selected_behold_cost(selected, selected_option)
    if cost.linked_exile_pair is None:
        return
    card = _validate_selection(host, proposal, source, selected, cost)
    journal = response.get('_mana_replacement_selections') or {}
    if (not isinstance(journal, Mapping) or len(journal) > 1
            or any(type(key) is not str or not key.startswith('zone.change:')
                   or not key.endswith(f':{card.ref}') or not isinstance(rows, (list, tuple))
                   for key, rows in journal.items())):
        raise CastProposalError('The Behold exile replacement journal is malformed')
    before = card.logical_object_id
    attachments = [host.state.cards[oid].ref for oid in card.attachments if oid in host.state.cards]
    change = (card, card.zone, card.controller, before, deepcopy(host._effective_card_data(card)),
              attachments, 'exile', 'exile', tuple(next(iter(journal.values()), ())))
    commit_card_cost_changes(host, proposal, source, result, (change,), required_paid_facts=())
    if card.zone == 'exile' and card.logical_object_id != before and not (card.is_token or card.is_spell_copy):
        result.beheld_linked_exile = (cost.linked_exile_pair, ExiledObjectIdentity(
            card.object_id, card.ref, card.logical_object_id))


def commit_behold_additional_costs(host, proposal, source, item, selected_option):
    payments = []
    for selected in selected_option.get('selected_additional_costs', ()):
        if selected.get('kind') != BEHOLD_ADDITIONAL_COST_KIND:
            continue
        cost = _selected_behold_cost(selected, selected_option)
        if cost.linked_exile_pair is not None:
            continue
        card = _validate_selection(host, proposal, source, selected, cost)
        identity = selected['selection']
        if card.zone == 'hand':
            begin_stack_cost_hand_reveal(host, identity['reveal'], item=item, source_ref=source.ref)
        host._log(proposal.seat, 'cost.behold', 'An object was beheld to pay a casting cost.',
            {'player': proposal.seat, 'object_id': card.object_id, 'zone': card.zone,
             'quality': cost.subtype, 'source': source.ref}, changed_objects=[card.object_id])
        payments.append({'player': proposal.seat, 'object_id': card.object_id,
            'logical_object_id': card.logical_object_id, 'zone': card.zone, 'quality': cost.subtype})
    return {'schema_version': 1, 'payments': payments} if payments else None
