from __future__ import annotations

"""Immutable ordered timestamp choice for replacement-adjusted Aura Roles."""

from dataclasses import dataclass
from typing import Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.domain_intent import DomainEffectIntent
from .model import (AutoContinue, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest)


@dataclass(frozen=True, slots=True)
class RoleTokenChoiceHandler:
    operation: str = 'create_role_token'
    handler_id: str = 'choice.token.role-timestamps.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 111.10', 'CR 303.4g', 'CR 613.7m', 'CR 704.5z')
    capability_dependencies: tuple[str, ...] = ('token.creation.predefined_role',)
    continuation_fields: tuple[str, ...] = ('controller', 'name', 'quantity', 'aura_target_ref',
        'characteristics', '_attachment_incarnation', '_role_preview', '_token_ids', '_choice_actor', '_replacement_selections')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'tokens', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('DomainEffectIntent create_token through canonical attached token commit',)
    replay_fixture: str = 'predefined-role-timestamp-order'
    test_modules: tuple[str, ...] = ('tests.test_predefined_roles',)

    def _intent(self, effect, actor, order, label):
        return DomainEffectIntent(actor=actor, operation='create_token', reason=label,
            effect=FrozenMap({**dict(effect), 'op': 'create_token', '_role_order': list(order)}))

    def prepare(self, effect, context):
        allowed = {'op', 'controller', 'name', 'quantity', 'aura_target_ref', 'characteristics', '_replacement_selections'}
        if (set(effect) - allowed or effect.get('controller') != context.actor
                or type(effect.get('quantity')) is not int or effect['quantity'] != 1
                or effect.get('name') not in {'Cursed', 'Monster', 'Royal', 'Sorcerer', 'Virtuous', 'Wicked', 'Young Hero'}
                or effect.get('characteristics', {}).get('type_line') != 'Token Enchantment — Aura Role'):
            raise SemanticChoiceError('Role creation requires one complete typed definition and controller')
        target_ref = effect.get('aura_target_ref')
        if target_ref is None:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='no selected Role attachment subject'))
        target = context.query.object(str(target_ref), zones=('battlefield',))
        if target is None or target.phased_out or 'creature' not in target.types:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='Role attachment subject is absent or illegal'))
        preview = tuple(context.query.token_creation_preview())
        if not preview:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='replacement creates no Role tokens'))
        count = sum(int(spec.get('quantity', 1)) for spec in preview)
        if count < 1 or any(spec.get('characteristics', {}).get('type_line') != 'Token Enchantment — Aura Role' for spec in preview):
            raise SemanticChoiceError('Role replacement needs a closed attached Role event')
        ids = tuple('role:' + str(index) for index in range(count))
        frozen = FrozenMap({**dict(effect), '_attachment_incarnation': target.logical_object_id,
            '_role_preview': preview, '_token_ids': ids, '_choice_actor': context.actor})
        if count == 1:
            return SemanticChoicePreparation(request=None, continuation_effect=frozen,
                preparation_intents=(self._intent(frozen, context.actor, (0,), context.stack_label),),
                auto_continue=AutoContinue(reason='one Role receives its timestamp'))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Order the Role tokens from earliest to latest timestamp.',
            choice=OrderingChoice(field_name='order', legal_refs=ids, visibility='public'),
            public_context=FrozenMap({'stack': context.stack_ref, 'tokens': [
                {'id': key, 'name': str(effect['name'])} for key in ids]})), continuation_effect=frozen)

    def complete(self, continuation, response, query):
        effect = continuation.effect
        ids = tuple(effect['_token_ids'])
        order = tuple(response.get('order', ()))
        target = query.object(str(effect['aura_target_ref']), zones=('battlefield',))
        if (len(order) != len(set(order)) or sorted(order) != sorted(ids)
                or target is None or target.logical_object_id != effect['_attachment_incarnation']
                or target.phased_out or 'creature' not in target.types
                or tuple(query.token_creation_preview()) != tuple(effect['_role_preview'])):
            raise SemanticChoiceError('Role order or attachment/replacement snapshot is stale or incomplete')
        return SemanticChoiceCompletion(intents=(self._intent(effect, str(effect['_choice_actor']),
            tuple(ids.index(key) for key in order), continuation.note),))


ROLE_TOKEN_CHOICE_HANDLERS = (RoleTokenChoiceHandler(),)
