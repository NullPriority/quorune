"""Pure lowering of live, source-owned paired keyword/P-T/ability grants."""
from __future__ import annotations

from dataclasses import dataclass, replace
from ..continuous_effect_model import ContinuousEffectOrigin, ContinuousEffectRelation, ContinuousObjectIdentity
from ..object_predicate import ObjectQuerySpec
from ..soulbond_model import SOULBOND_GRANT_HANDLER, SOULBOND_GRANT_CAPABILITY
from .attached_continuous import AttachedFixedCharacteristicsHandler, ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID
from .context import SemanticNodeError
from .fixed_characteristic_effects import fixed_characteristic_effects


@dataclass(frozen=True, slots=True)
class SoulbondPairedGrantHandler:
    handler_id: str = SOULBOND_GRANT_HANDLER
    schema_version: int = 1
    family: str = 'continuous.soulbond.paired_grant'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('702.95b','611.3a','613.1f','613.4c')
    capability_dependencies: tuple[str, ...] = (SOULBOND_GRANT_CAPABILITY,)

    def validate(self, descriptor):
        if (descriptor.get('handler_id') != self.handler_id
                or descriptor.get('condition') != {'relation':'source_pair', 'types_all':['creature']}):
            raise SemanticNodeError('A paired grant requires its exact source relation')
        node = AttachedFixedCharacteristicsHandler().validate({**descriptor,
            'handler_id':ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID,
            'condition':{'relation':'source_attached_object', 'types_all':['creature']}})
        if (node.type_operations or node.color_operations or node.remove_abilities or node.remove_all_abilities
                or node.base_power is not None or node.quantity is not None):
            raise SemanticNodeError('Paired grants require a closed keyword, typed ability or additive P/T modifier')
        return node

    def lower(self, descriptor, context):
        node = self.validate(descriptor)
        if context.paired_object is None:
            return ()
        if not context.source_logical_object_id:
            raise SemanticNodeError('Paired static sources require an exact incarnation')
        effects = []
        targets = (('self', ContinuousEffectRelation.SOURCE_OBJECT,
                    ContinuousObjectIdentity(context.source_object_id, context.source_logical_object_id)),
                   ('partner', ContinuousEffectRelation.SOURCE_PAIRED_WITH_OBJECT, context.paired_object))
        for label, relation, identity in targets:
            scoped = replace(context, component_id=context.component_id + ':' + label)
            effects.extend(fixed_characteristic_effects(node, scoped, common={
                'source_id':context.source_object_id, 'timestamp':context.source_timestamp,
                'origin':ContinuousEffectOrigin.STATIC_ABILITY, 'relation':relation,
                'related_object':identity, 'applies':ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))}))
        return tuple(effects)
