"""Live receiver-qualified Aura characteristics through existing layer emission."""
from dataclasses import dataclass, replace
from collections.abc import Mapping

from ..characteristic_fragments import CharacteristicQuantityReduction, CharacteristicQuantityScope
from ..continuous_effect_model import Layer
from ..object_predicate import ObjectQuerySpec
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from .attached_continuous import AttachedFixedCharacteristicsHandler, ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID
from .component_registry import exact_fields
from .context import SemanticNodeError

HANDLER = 'continuous.attached.qualified-characteristics.v1'
CAPABILITY = 'continuous.attached.qualified_characteristics'
DEPENDENCIES = (CAPABILITY, 'continuous.attached.fixed_characteristics',
                'continuous.characteristics.fixed_public_setting')
CREATURE_QUERY = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',))
VEHICLE_QUERY = ObjectQuerySpec(zones=('battlefield',), subtypes_all=('vehicle',))


def _base_descriptor(descriptor):
    return {'handler_id': ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID, 'schema_version': 1,
            'event': 'characteristics.evaluate',
            'condition': {'relation': 'source_attached_object', 'types_all': []},
            'modifier': descriptor['modifier']}


def _validate_qualified_modifier(query, node):
    if query == VEHICLE_QUERY:
        operations = node.type_operations
        if (len(operations) != 1 or operations[0].op != 'add_types'
                or operations[0].field != 'card_types'
                or tuple(str(v).casefold() for v in operations[0].value) != ('creature',)
                or node.color_operations or node.add_abilities or node.remove_abilities
                or node.remove_all_abilities or node.add_rules_text or node.add_ability_fragments
                or node.power or node.toughness or node.base_power is not None
                or node.base_toughness is not None or node.quantity is not None):
            raise SemanticNodeError('Vehicle qualification permits only preserving types while adding Creature')
        return
    if (node.type_operations or node.color_operations or node.remove_all_abilities
            or node.add_rules_text or node.add_ability_fragments
            or any(value not in FIXED_CHARACTERISTIC_KEYWORDS
                   for value in (*node.add_abilities, *node.remove_abilities))):
        raise SemanticNodeError('Creature qualification permits represented stat and keyword changes')
    quantity = node.quantity
    if quantity is not None and (
            quantity.schema_version not in {1, 2}
            or quantity.scope is not CharacteristicQuantityScope.CONTROLLER_ZONE
            or quantity.query is None or quantity.query.zones != ('battlefield',)
            or quantity.reduction is not CharacteristicQuantityReduction.OBJECT_COUNT
            or quantity.multiplier != 1 or quantity.offset):
        raise SemanticNodeError('Qualified attachment quantities require an unscaled current controlled battlefield count')


@dataclass(frozen=True, slots=True)
class QualifiedAttachedCharacteristicsHandler:
    handler_id: str = HANDLER
    schema_version: int = 1
    family: str = CAPABILITY
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('303.4', '611.3a', '611.3b', '611.3c',
        '613.1d', '613.1f', '613.4b', '613.4c', '613.6', '613.7', '613.8')
    capability_dependencies: tuple[str, ...] = DEPENDENCIES

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'receiver_query', 'modifier'},
                     field='Qualified attachment characteristics')
        if (descriptor['handler_id'] != self.handler_id or descriptor['event'] != self.event
                or type(descriptor['schema_version']) is not int or descriptor['schema_version'] != 1
                or not isinstance(descriptor['receiver_query'], Mapping)):
            raise SemanticNodeError('Qualified attachment characteristic identity changed')
        query = ObjectQuerySpec.from_dict(descriptor['receiver_query'])
        if query not in (CREATURE_QUERY, VEHICLE_QUERY):
            raise SemanticNodeError('Qualified attachments require a closed current Creature or Vehicle receiver')
        node = AttachedFixedCharacteristicsHandler().validate(_base_descriptor(descriptor))
        _validate_qualified_modifier(query, node)
        return query, node

    def descriptor_source_component_layer(self, descriptor):
        _, node = self.validate(descriptor)
        return Layer.TYPE if node.type_operations else Layer.ABILITY

    def lower(self, descriptor, context):
        query, _ = self.validate(descriptor)
        effects = AttachedFixedCharacteristicsHandler().lower(_base_descriptor(descriptor), context)
        return tuple(replace(effect, applies=query) for effect in effects)
