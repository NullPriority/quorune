from __future__ import annotations

"""One subtype-addition effect with independently closed quoted children."""

from copy import deepcopy
from dataclasses import dataclass

from ..object_predicate import ObjectQuerySpec
from ..type_subtype_vocabulary import CARD_TYPE_SUBTYPES
from .attached_continuous import _attached_characteristics_node
from .component_registry import exact_fields
from .composite_grants import COMPOSITE_GRANT_HANDLER, CompositeAbilityGrantHandler
from .context import SemanticNodeError
from .fixed_characteristic_settings import FixedCharacteristicSettingsHandler


SUBTYPE_COMPOSITE_GRANT_HANDLER = 'continuous.characteristics.subtype-composite-grant.v3'
SUBTYPE_COMPOSITE_GRANT_CAPABILITY = 'continuous.ability.fixed_subtype_composite_grant'


@dataclass(frozen=True, slots=True)
class FixedSubtypeCompositeGrantHandler(FixedCharacteristicSettingsHandler):
    handler_id: str = SUBTYPE_COMPOSITE_GRANT_HANDLER
    schema_version: int = 3
    family: str = SUBTYPE_COMPOSITE_GRANT_CAPABILITY
    capability_dependencies: tuple[str, ...] = (
        SUBTYPE_COMPOSITE_GRANT_CAPABILITY,
        *CompositeAbilityGrantHandler().capability_dependencies,
    )

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'target', 'modifier'},
            field='subtype composite')
        if (descriptor['handler_id'] != self.handler_id
                or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event):
            raise SemanticNodeError('Subtype composite identity changed')
        full = _attached_characteristics_node((), descriptor['modifier'])
        # The older closed child/selector owner still validates all remaining fields.
        children_only = deepcopy(descriptor)
        children_only.update(handler_id=COMPOSITE_GRANT_HANDLER, schema_version=1)
        children_only['modifier']['type_operations'] = []
        target, predicate, _ = CompositeAbilityGrantHandler().validate(children_only)
        if (predicate.excluded_controllers or predicate.include_phased_out
                or target['controller'] not in {None, 'any', 'source_controller', 'source_opponents'}):
            raise SemanticNodeError('Subtype composites reserve controller and phasing authority')
        if predicate != ObjectQuerySpec(zones=predicate.zones, types_all=predicate.types_all, token=predicate.token):
            raise SemanticNodeError('Subtype composites require a stable permanent-type and token scope')
        if target['kind'] == 'attached' and predicate != ObjectQuerySpec(
                zones=('battlefield',), types_all=predicate.types_all):
            raise SemanticNodeError('Attached subtype composites require one ordinary subject')
        if not full.type_operations or full.remove_all_abilities:
            raise SemanticNodeError('Subtype composites require additive subtype operations')
        supported = set()
        for kind in predicate.types_all:
            if kind in {'creature', 'kindred', 'artifact'}:
                supported.update(CARD_TYPE_SUBTYPES[kind])
        for operation in full.type_operations:
            if (operation.op != 'add_types' or operation.field != 'subtypes'
                    or not operation.value or any(str(value).casefold() not in supported for value in operation.value)):
                raise SemanticNodeError('Added subtypes must correspond to a guaranteed receiver type')
        return target, predicate, full
