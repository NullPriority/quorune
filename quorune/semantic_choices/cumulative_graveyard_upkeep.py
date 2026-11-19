from __future__ import annotations

"""Collect per-age cards and private owner orders before paying the whole cost."""

from collections.abc import Mapping

from ..cumulative_graveyard_upkeep import GraveyardLibraryCostSpec, GraveyardLibraryCostUnit, STATE_FIELD
from ..library_order_model import LibraryArrivalOrder
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.graveyard_library_cost_intent import GraveyardLibraryCostBatchIntent
from ..semantic_runtime.intents import PlaceCountersIntent
from .model import (AutoContinue, ObjectChoice, OrderingChoice, ScalarChoice, SemanticChoiceCompletion,
                    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest)
from .source_maintenance import _declined_source_sacrifice

_STATE_FIELDS = {'phase', 'payer', 'source_identity', 'age', 'units', 'orders', 'incarnations', 'owner', 'label'}
_TRANSPORT = {'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema'}


def _spec(effect):
    raw = {k: v for k, v in effect.items() if k != STATE_FIELD}
    try:return GraveyardLibraryCostSpec.from_effect(raw, actor=raw.get('player'), source=raw.get('source')), raw
    except (TypeError, ValueError, KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc


def _units(state):
    if not isinstance(state['units'], (list, tuple)):raise SemanticChoiceError('Graveyard cost units must be a sequence')
    units = []
    for row in state['units']:
        if not isinstance(row, Mapping) or set(row) != {'owner', 'refs'} or not isinstance(row['refs'], (list, tuple)):
            raise SemanticChoiceError('Graveyard cost unit fields changed')
        try:units.append(GraveyardLibraryCostUnit(row['owner'], tuple(row['refs'])))
        except ValueError as exc:raise SemanticChoiceError(str(exc)) from exc
    return tuple(units)


def _pools(query, excluded=()):
    result = {seat: [] for seat in query.active_seats}
    for row in query.objects(zones=('graveyard',)):
        if row.owner in result and not row.token and row.known_to_actor and row.ref not in excluded:
            result[row.owner].append(row.ref)
    return {seat: tuple(refs) for seat, refs in result.items()}


def _validated_state(effect, controller, query):
    spec, raw = _spec(effect); state = effect.get(STATE_FIELD)
    if (not isinstance(state, Mapping) or set(state) != _STATE_FIELDS or state['payer'] != controller
            or controller not in query.active_seats or state['phase'] not in {'price', 'graveyard', 'cards', 'order', 'zero'}
            or any(type(state[k]) is not str or not state[k] for k in ('payer', 'source_identity', 'label'))):
        raise SemanticChoiceError('Grouped graveyard cost continuation changed its original payer')
    try:spec.total_cards(state['age'])
    except ValueError as exc:raise SemanticChoiceError(str(exc)) from exc
    units = _units(state); refs = tuple(ref for unit in units for ref in unit.card_refs)
    if (len(units) > state['age'] or len(set(refs)) != len(refs)
            or not isinstance(state['incarnations'], Mapping) or set(state['incarnations']) != set(refs)
            or any(type(v) is not str or not v for v in state['incarnations'].values())
            or any(len(unit.card_refs) != spec.unit_size for unit in units)
            or not isinstance(state['orders'], (list, tuple)) or len(state['orders']) > len(units)):
        raise SemanticChoiceError('Grouped graveyard cost lost its selected units or incarnations')
    for unit in units:
        for ref in unit.card_refs:
            row = query.object(ref, zones=('graveyard',))
            if row is None or row.token or row.owner != unit.graveyard_owner or row.logical_object_id != state['incarnations'][ref]:
                raise SemanticChoiceError('A selected graveyard cost card changed incarnation or owner')
    for unit, order in zip(units, state['orders']):
        if not isinstance(order, (list, tuple)) or len(order) != spec.unit_size or set(order) != set(unit.card_refs):
            raise SemanticChoiceError('A library cost order changed its exact unit')
    phase = state['phase']; chooser = units[len(state['orders'])].graveyard_owner if phase == 'order' and len(state['orders']) < len(units) else controller
    if (effect['player'] != chooser or phase == 'order' and (len(units) != state['age'] or not units or len(state['orders']) >= len(units))
            or phase in {'price', 'zero'} and (units or state['orders'])
            or phase in {'graveyard', 'cards'} and (len(units) >= state['age'] or state['orders'])
            or phase == 'zero' and state['age'] != 0
            or phase == 'cards' and state['owner'] not in query.active_seats
            or phase != 'cards' and state['owner'] is not None):
        raise SemanticChoiceError('Grouped graveyard cost phase or current chooser changed')
    return spec, raw, dict(state), units


def _next(effect, state):
    player = state['payer']
    if state['phase'] == 'order':player = state['units'][len(state['orders'])]['owner']
    return {**effect, 'player': player, STATE_FIELD: state}


def _decline(effect, state, query):
    return _declined_source_sacrifice({'source': effect['source'], '_choice_actor': state['payer'],
        '_source_logical_object_id': state['source_identity'], '_stack_label': state['label']}, query)


def prepare_cumulative_graveyard_upkeep(effect, context):
    spec, raw = _spec(effect)
    if context.actor != effect['player']:
        raise SemanticChoiceError('Grouped upkeep choice changed its current chooser')
    if STATE_FIELD not in effect:
        if context.actor != context.stack_controller or effect['player'] != context.actor:
            raise SemanticChoiceError('Grouped upkeep cost changed its trigger controller')
        source = context.query.object(context.source_ref, zones=('battlefield',)) if context.source_ref else None
        if source is None or source.phased_out or source.logical_object_id != context.source_logical_object_id:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue('Original upkeep permanent left battlefield'))
        if raw['source'] != source.ref or not context.source_logical_object_id:
            raise SemanticChoiceError('Grouped upkeep cost lost its original source binding')
        state = dict(phase='price', payer=context.actor, source_identity=context.source_logical_object_id,
                     age=0, units=[], orders=[], incarnations={}, owner=None, label=context.stack_label)
        return SemanticChoicePreparation(None, FrozenMap(effect), preparation_intents=(PlaceCountersIntent(
            actor=context.actor, object_refs=(source.ref,), counter_name='age', amount=1,
            reason=context.stack_label, source_ref=source.ref),), auto_continue=AutoContinue(
                'Commit age placement before pricing', prepend_effects=(FrozenMap(_next(raw, state)),)))
    spec, raw, state, units = _validated_state(effect, context.stack_controller, context.query)
    if state['source_identity'] != context.source_logical_object_id or state['label'] != context.stack_label:
        raise SemanticChoiceError('Grouped upkeep stack source or label changed')
    if state['phase'] == 'price':
        source = context.query.object(effect['source'], zones=('battlefield',)) if effect['source'] else None
        if source is None or source.logical_object_id != state['source_identity']:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue('Original upkeep source left'))
        state['age'] = source.counters.get('age', 0); spec.total_cards(state['age'])
        state['phase'] = 'graveyard' if state['age'] else 'zero'
    selected = tuple(ref for unit in units for ref in unit.card_refs)
    pools = _pools(context.query, selected); phase = state['phase']
    if phase == 'zero':choice = ScalarChoice(field_name='pay', legal_values=(True, False))
    elif phase == 'graveyard':
        owners = tuple(owner for owner, refs in pools.items() if len(refs) >= spec.unit_size) if spec.can_pay(state['age'] - len(units), pools) else ()
        choice = ScalarChoice(field_name='choice', legal_values=(*owners, 'decline'))
    elif phase == 'cards':
        refs = pools.get(state['owner'], ())
        if len(refs) < spec.unit_size:raise SemanticChoiceError('Selected graveyard can no longer pay a full unit')
        choice = ObjectChoice('cards', refs, ('graveyard',), minimum=0, maximum=spec.unit_size,
                              optional=True, allowed_cardinalities=(0, spec.unit_size))
    else:
        choice = OrderingChoice('cards', units[len(state['orders'])].card_refs, visibility='actor_private',
                                schema_extras=FrozenMap({'order': 'bottom_to_top'}))
    return SemanticChoicePreparation(SemanticChoiceRequest('Choose the complete upkeep cost or order its library unit.',
        choice, FrozenMap({'age_counters': state['age'], 'chosen_units': len(units), 'payment_kind': 'graveyard_library',
                           'phase': phase, 'unit_size': spec.unit_size})), FrozenMap(_next(raw, state)))


def complete_cumulative_graveyard_upkeep(continuation, response, query):
    spec, raw, state, units = _validated_state(continuation.effect, continuation.semantic_frame.controller, query)
    phase = state['phase']; expected = 'pay' if phase == 'zero' else 'choice' if phase == 'graveyard' else 'cards'
    if phase == 'price' or set(response) - _TRANSPORT - {expected}:
        raise SemanticChoiceError('Grouped graveyard cost response fields changed')
    if phase == 'zero':
        if type(response.get('pay')) is not bool:raise SemanticChoiceError('Empty upkeep needs pay or decline')
        return SemanticChoiceCompletion() if response['pay'] else _decline(raw, state, query)
    if phase == 'graveyard':
        owner = response.get('choice'); pools = _pools(query, state['incarnations'])
        if owner == 'decline':return _decline(raw, state, query)
        if type(owner) is not str or len(pools.get(owner, ())) < spec.unit_size or not spec.can_pay(state['age'] - len(units), pools):
            raise SemanticChoiceError('Choose a graveyard that can pay the entire remaining cost')
        state.update(phase='cards', owner=owner)
    else:
        refs = response.get('cards')
        if not isinstance(refs, (tuple, list)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
            raise SemanticChoiceError('Grouped graveyard cards must be distinct references')
        if phase == 'cards':
            if not refs:return _decline(raw, state, query)
            if len(refs) != spec.unit_size or not set(refs) <= set(_pools(query, state['incarnations']).get(state['owner'], ())):
                raise SemanticChoiceError('Choose one complete unit from the selected graveyard')
            state['units'] = [*state['units'], {'owner': state['owner'], 'refs': list(refs)}]
            state['incarnations'] = {**state['incarnations'], **{ref: query.object(ref, zones=('graveyard',)).logical_object_id for ref in refs}}
            state.update(owner=None, phase='order' if len(state['units']) == state['age'] else 'graveyard')
        else:
            unit = units[len(state['orders'])]
            if len(refs) != spec.unit_size or set(refs) != set(unit.card_refs):
                raise SemanticChoiceError('Order every card in the current cost unit exactly once')
            state['orders'] = [*state['orders'], list(refs)]
            if len(state['orders']) == state['age']:
                spec.validate_units(state['age'], units, _pools(query))
                orders = tuple(LibraryArrivalOrder(unit.graveyard_owner, 'bottom', tuple(order))
                               for unit, order in zip(units, state['orders'], strict=True))
                return SemanticChoiceCompletion(intents=(GraveyardLibraryCostBatchIntent(
                    actor=state['payer'], object_refs=tuple(ref for order in orders for ref in order.members),
                    expected_zones=('graveyard',), destination='library', reason=state['label'],
                    orders=orders, incarnations=FrozenMap(state['incarnations']), unit_size=spec.unit_size),))
    return SemanticChoiceCompletion(repeat_effect=FrozenMap(_next(raw, state)))
