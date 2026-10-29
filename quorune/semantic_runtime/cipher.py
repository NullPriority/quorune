"""Cipher's exiled-card grant uses the ordinary layer6 fragment owner."""
from dataclasses import dataclass
from ..cipher_model import CIPHER_CAPABILITY, CIPHER_GRANT_HANDLER, CIPHER_TRIGGER_KEY, CIPHER_HANDLER
from ..ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_to_dict
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousOperation, Layer
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class CipherSpellDeclarationHandler:
    handler_id: str = CIPHER_HANDLER
    schema_version: int = 1
    family: str = 'casting.lifecycle.cipher'
    event: str = 'resolve'
    rule_references: tuple[str, ...] = ('702.99a',)
    capability_dependencies: tuple[str, ...] = (CIPHER_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event'}, field='Cipher spell')
        if descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1 or descriptor['event'] != self.event:
            raise SemanticNodeError('Cipher spell declaration identity is malformed')
        return descriptor

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()


@dataclass(frozen=True, slots=True)
class CipherEncodingGrantHandler:
    handler_id: str = CIPHER_GRANT_HANDLER
    schema_version: int = 1
    family: str = 'continuous.ability.cipher_encoding'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('702.99a', '702.99b', '702.99c', '613.1f')
    capability_dependencies: tuple[str, ...] = (CIPHER_CAPABILITY, 'continuous.ability.fixed_query_grant')

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event'}, field='Cipher grant')
        if descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1 or descriptor['event'] != self.event:
            raise SemanticNodeError('Cipher grant identity is malformed')
        return descriptor

    def lower(self, descriptor, context):
        self.validate(descriptor)
        if context.cipher_encoded_object is None:
            return ()
        fragment = GrantedTriggeredAbilitySpec(ability_id='cipher:' + context.source_ref,
            semantic_key=CIPHER_TRIGGER_KEY, event='damage.dealt.self', label='Cipher', schema_version=2,
            cipher_card_ref=context.source_ref)
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}:cipher',
            source_id=context.source_object_id, layer=Layer.ABILITY, sublayer='6', timestamp=context.source_timestamp,
            operations=(ContinuousOperation('add_ability_fragment', ability_fragment_to_dict(fragment)),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY, applies=ObjectQuerySpec(zones=('battlefield',)),
            relation=ContinuousEffectRelation.SOURCE_OBJECT, related_object=context.cipher_encoded_object),)
