"""Shared replacement-aware destination plans for creatures entering attacking."""
from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import CreateTokenIntent
from .model import (AutoContinue, DecisionMapChoice, SemanticChoiceCompletion,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest)


def legal_attacking_destinations(query, actor):
    opponents = {seat for seat in query.active_seats if seat != actor}
    destinations = set(opponents)
    for row in query.objects(zones=('battlefield',)):
        if 'planeswalker' in row.types and row.controller in opponents:
            destinations.add(row.ref)
        elif 'battle' in row.types and row.battle_protector in opponents:
            destinations.add(row.ref)
    seat_order = {seat: index for index, seat in enumerate(query.seats)}
    return tuple(sorted(destinations, key=lambda value: (
        0 if value in seat_order else 1, seat_order.get(value, 0), value)))


def prepare_attacking_destinations(effect, context, *, operation, label, prefix):
    destinations = legal_attacking_destinations(context.query, context.actor)
    if not destinations:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason='no defending recipient'))
    preview = tuple(context.query.token_creation_preview())
    groups = tuple(str(row.get('_attacking_group') or '') for row in preview
        for _ in range(int(row.get('quantity', 1))) if row.get('_attacking_group') is not None)
    if not preview or any(group != 'any' for group in groups):
        raise SemanticChoiceError(f'{label} token replacement preview is malformed')
    token_ids = tuple(f'{prefix}:{index}' for index in range(len(groups)))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(prompt=f'Choose what each {label} token is attacking.',
            choice=DecisionMapChoice(field_name='attacking', legal_refs=token_ids,
                required=len(token_ids), legal_values=destinations),
            public_context=FrozenMap({'stack': context.stack_ref, 'operation': operation,
                'destinations': destinations})),
        continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
            '_token_ids': token_ids, '_destinations': destinations,
            '_stack_label': context.stack_label, '_preview_specs': preview}))


def complete_attacking_destinations(effect, response, query, *, name, tapped, sacrifice, label):
    raw = response.get('attacking')
    if not isinstance(raw, Mapping):
        raise SemanticChoiceError(f'{label} assignments must be an object map')
    decisions = {str(key): str(value) for key, value in raw.items()}
    token_ids = tuple(str(value) for value in effect.get('_token_ids', ()))
    destinations = set(str(value) for value in effect.get('_destinations', ()))
    actor = str(effect.get('_choice_actor') or '')
    if (set(decisions) != set(token_ids) or any(value not in destinations for value in decisions.values())
            or not destinations.issubset(set(legal_attacking_destinations(query, actor)))
            or tuple(query.token_creation_preview()) != tuple(effect.get('_preview_specs', ()))):
        raise SemanticChoiceError(f'{label} assignments are stale or incomplete')
    return SemanticChoiceCompletion(intents=(CreateTokenIntent(actor=actor, controller=actor,
        name=name, quantity=int(effect.get('quantity', 0)),
        characteristics=FrozenMap(dict(effect.get('characteristics') or {})), tapped=tapped,
        attacking_assignments=tuple(decisions[token_id] for token_id in token_ids),
        attacking_groups=tuple('any' for _ in range(int(effect.get('quantity', 0)))),
        sacrifice_at_end_step=sacrifice, reason=str(effect.get('_stack_label') or label),
        replacement_selections=tuple(effect.get('_replacement_selections', ()))),))
