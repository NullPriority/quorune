"""Actor-scoped, non-target card choice followed by the existing return owner."""
from collections.abc import Mapping
from dataclasses import dataclass
import re

from ..public_characteristic_aggregates import reduce_public_characteristic_values
from ..replacement.immutable import FrozenMap
from ..rules.graveyard_return_choices import OPERATION, CAPABILITY, validate_return_choice
from ..semantic_runtime.intents import ReturnGraveyardCardToOwnerHandIntent
from .model import (AutoContinue, ObjectChoice, SemanticChoicePreparation, SemanticChoiceRequest,
    SemanticChoiceCompletion, SemanticChoiceError)


def _return_identity(row):
    if any(type(value) is not str or not value for value in (row.ref, row.object_id, row.logical_object_id)):
        raise SemanticChoiceError('The selected graveyard identity is incomplete')
    prefix = row.object_id+'@'
    suffix = row.logical_object_id.removeprefix(prefix)
    if not row.logical_object_id.startswith(prefix) or re.fullmatch(r'0|[1-9][0-9]*', suffix) is None:
        raise SemanticChoiceError('The selected graveyard incarnation is malformed')
    return FrozenMap({'ref':row.ref, 'object_id':row.object_id, 'zone_change_counter':int(suffix)})


def _eligible_return_rows(query, actor, selection):
    refs = query.choice_candidate_refs()
    rows = tuple(query.object(ref, zones=('graveyard',)) for ref in refs)
    if (len(refs) != len(set(refs)) or any(row is None or row.owner != actor
            or row.token or not row.logical_object_id or not selection.matches(row) for row in rows)):
        raise SemanticChoiceError('Graveyard return candidates do not match their owned public query')
    if not selection.greatest_power or not rows:
        return rows
    powers = tuple(0 if row.effective_power is None else row.effective_power for row in rows)
    maximum = reduce_public_characteristic_values(powers, 'maximum')
    return tuple(row for row, power in zip(rows, powers) if power == maximum)


def _return_intent(row, *, actor, reason):
    return ReturnGraveyardCardToOwnerHandIntent(actor=actor, object_ref=row.ref, reason=reason,
                                               target_snapshot=_return_identity(row))


@dataclass(frozen=True, slots=True)
class GraveyardReturnChoiceHandler:
    operation: str = OPERATION
    handler_id: str = 'choice.graveyard.own-return.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('109.5', '115.10a', '400.3', '400.7', '608.2c', '608.2d')
    capability_dependencies: tuple[str, ...] = (CAPABILITY, 'card.return.own_graveyard_to_owner_hand')
    continuation_fields: tuple[str, ...] = ('player', 'selection', '_choice_actor', '_legal_refs',
        '_legal_logical_ids', '_stack_label')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'objects', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('ReturnGraveyardCardToOwnerHandIntent', 'return_to_hand')
    replay_fixture: str = 'optional-own-graveyard-return'
    test_modules: tuple[str, ...] = ('tests.test_graveyard_return_choices',)

    def prepare(self, effect, context):
        try:
            selection = validate_return_choice(effect, actor=context.actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if selection.greatest_power and context.actor != context.stack_controller:
            raise SemanticChoiceError('Greatest-power return requires the resolving controller')
        rows = _eligible_return_rows(context.query, context.actor, selection)
        if not rows:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='No eligible card in your graveyard'))
        if selection.greatest_power and len(rows) == 1:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                preparation_intents=(_return_intent(rows[0], actor=context.actor, reason=context.stack_label),),
                auto_continue=AutoContinue(reason='Return the sole greatest-power creature card'),
            )
        for row in rows:
            _return_identity(row)
        refs = tuple(row.ref for row in rows)
        optional = not selection.greatest_power
        prompt = ('You may return a card from your graveyard to your hand.' if optional
                  else 'Choose a tied greatest-power creature card to return to your hand.')
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(prompt=prompt,
                choice=ObjectChoice(field_name='card', legal_refs=refs, zones=('graveyard',),
                    minimum=0 if optional else 1, maximum=1, optional=optional, owner_relation='actor'),
                public_context=FrozenMap({'stack': context.stack_ref, 'operation': self.operation,
                    'objects': [{'id': row.ref, 'name': row.printed_name} for row in rows]})),
            continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
                '_legal_refs': refs, '_legal_logical_ids': {row.ref: row.logical_object_id for row in rows},
                '_stack_label': context.stack_label}))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = effect.get('_choice_actor')
        try:
            selection = validate_return_choice(effect, actor=actor, continuation=True)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        refs, identities = effect['_legal_refs'], effect['_legal_logical_ids']
        label = effect['_stack_label']
        if (type(actor) is not str or not actor or not isinstance(refs, (tuple, list))
                or any(type(ref) is not str or not ref for ref in refs) or len(refs) != len(set(refs))
                or not isinstance(identities, Mapping) or set(identities) != set(refs)
                or any(type(value) is not str or not value for value in identities.values())
                or type(label) is not str or not label):
            raise SemanticChoiceError('Graveyard return continuation identities are malformed')
        selected = response.get('card')
        if selection.greatest_power:
            current = _eligible_return_rows(query, actor, selection)
            if (len(current) < 2 or tuple(refs) != tuple(row.ref for row in current)
                    or dict(identities) != {row.ref: row.logical_object_id for row in current}):
                raise SemanticChoiceError('The greatest-power graveyard choices changed')
            if type(selected) is not str or selected not in refs:
                raise SemanticChoiceError('Choose one of the tied greatest-power creature cards')
        elif selected is None or selected == '':
            return SemanticChoiceCompletion()
        elif type(selected) is not str or selected not in refs or selected not in query.choice_candidate_refs():
            raise SemanticChoiceError('Choose one of the eligible graveyard cards or decline')
        row = query.object(selected, zones=('graveyard',))
        if (row is None or row.owner != actor or row.token or row.logical_object_id != identities[selected]
                or not selection.matches(row)):
            raise SemanticChoiceError('The selected graveyard incarnation is no longer eligible')
        return SemanticChoiceCompletion(intents=(_return_intent(row, actor=actor, reason=label),))
