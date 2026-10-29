from __future__ import annotations

"""One layer plan for independently closed quoted grants and ability removal."""

from dataclasses import dataclass
from ..ability_fragments import (
    ability_fragment_from_dict,GrantedActivatedAbilitySpec,GrantedTriggeredAbilitySpec,StaticComponentSpec,
)
from ..basic_land_types import LAND_SUBTYPES
from ..object_predicate import ObjectQuerySpec
from .attached_continuous import _attached_characteristics_node
from .component_registry import exact_fields
from .context import SemanticNodeError
from .continuous_components import FixedQueryAbilityGrantHandler
from .fixed_characteristic_settings import FixedCharacteristicSettingsHandler


COMPOSITE_GRANT_HANDLER = 'continuous.characteristics.composite-grant.v1'
COMPOSITE_GRANT_CAPABILITY = 'continuous.ability.fixed_composite_grant'
TYPE_REPLACEMENT_GRANT_HANDLER = 'continuous.characteristics.type-replacement-grant.v2'
TYPE_REPLACEMENT_GRANT_CAPABILITY = 'continuous.characteristics.fixed_type_replacement'


@dataclass(frozen=True, slots=True)
class CompositeAbilityGrantHandler(FixedCharacteristicSettingsHandler):
    handler_id: str = COMPOSITE_GRANT_HANDLER
    family: str = 'continuous.ability.fixed_composite_grant'
    capability_dependencies: tuple[str,...] = (
        COMPOSITE_GRANT_CAPABILITY,'continuous.characteristics.fixed_public_setting',
        'continuous.ability.fixed_query_grant',
    )

    def validate(self,descriptor):
        exact_fields(descriptor,{'handler_id','schema_version','event','target','modifier'},field='composite grant')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=self.schema_version or descriptor['event']!=self.event):
            raise SemanticNodeError('Composite grant identity is malformed')
        target=descriptor['target']
        exact_fields(target,{'kind','controller','predicate','exclude_source'},field='composite grant target')
        if target['kind'] not in {'attached','fixed_query'}:
            raise SemanticNodeError('Composite grants need one current attachment or public query')
        if target['kind']=='attached' and (target['controller'] is not None or target['exclude_source'] is not False):
            raise SemanticNodeError('Attachments reserve controller and exclusion fields')
        predicate=ObjectQuerySpec.from_dict(target['predicate'])
        node=_attached_characteristics_node((),descriptor['modifier'])
        if ((self.schema_version==1 and node.color_operations) or node.power or node.toughness or node.base_power is not None
                or node.quantity is not None or node.quantity_power or node.quantity_toughness
                or node.remove_abilities or node.add_rules_text or node.add_abilities):
            raise SemanticNodeError('Composite grants reserve unrepresented characteristic operations')
        fragments=tuple(ability_fragment_from_dict(value) for value in node.add_ability_fragments)
        if not (0 if self.schema_version==2 else 1)<=len(fragments)<=4 or any(not isinstance(fragment,(GrantedActivatedAbilitySpec,GrantedTriggeredAbilitySpec,StaticComponentSpec))
                or isinstance(fragment,StaticComponentSpec) and fragment.schema_version not in {2,3} for fragment in fragments):
            raise SemanticNodeError('Composite grants require independently closed quoted children')
        if len({fragment.semantic_key for fragment in fragments})!=len(fragments):
            raise SemanticNodeError('Each composite child has a distinct identity')
        if self.schema_version==2:
            from ..type_subtype_vocabulary import CARD_TYPE_SUBTYPES
            operations=node.type_operations
            if (target['kind']!='attached' or predicate!=ObjectQuerySpec(zones=('battlefield',))
                    or not 1<=len(operations)<=2 or operations[0].op!='set_types' or operations[0].field!='card_types'
                    or len(operations[0].value)!=1 or str(operations[0].value[0]).casefold() not in {'artifact','enchantment','land'}):
                raise SemanticNodeError('Type replacement requires one stable attachment and one represented permanent type')
            kind=str(operations[0].value[0]).casefold()
            if len(operations)==2 and (operations[1].op!='set_types' or operations[1].field!='subtypes'
                    or kind=='land' or any(str(v).casefold() not in CARD_TYPE_SUBTYPES[kind] for v in operations[1].value)):
                raise SemanticNodeError('Replacement subtypes must belong to the represented nonland type')
            if node.color_operations and (len(node.color_operations)!=1 or node.color_operations[0].op!='remove_all_colors'):
                raise SemanticNodeError('Type replacement admits only an explicit colorless result')
        elif node.type_operations:
            if (len(node.type_operations)!=1 or not node.remove_all_abilities
                    or node.type_operations[0].op!='remove_types' or node.type_operations[0].field!='subtypes'
                    or tuple(str(v).casefold() for v in node.type_operations[0].value)!=tuple(sorted(LAND_SUBTYPES))
                    or target['kind']=='fixed_query' and (predicate.subtypes_all or predicate.subtypes_any or predicate.any_of)):
                raise SemanticNodeError('Land subtype removal needs the exact pinned set and a stable recipient scope')
        if fragments:
            query=FixedQueryAbilityGrantHandler()
            query.validate({'handler_id':query.handler_id,'schema_version':1,'event':self.event,
                'condition':{'target_controller':target['controller'] if target['kind']=='fixed_query' else 'any',
                    'predicate':predicate.to_dict(),'exclude_source':target['exclude_source']},
                'modifier':{'add_ability_fragments':list(node.add_ability_fragments)}})
        return target,predicate,node


@dataclass(frozen=True, slots=True)
class FixedTypeReplacementGrantHandler(CompositeAbilityGrantHandler):
    handler_id: str = TYPE_REPLACEMENT_GRANT_HANDLER
    schema_version: int = 2
    family: str = 'continuous.characteristics.fixed_type_replacement'
    capability_dependencies: tuple[str,...] = (
        TYPE_REPLACEMENT_GRANT_CAPABILITY,*CompositeAbilityGrantHandler().capability_dependencies,
    )
