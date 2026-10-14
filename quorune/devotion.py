from __future__ import annotations

"""CR 700.5 devotion after copy/control/text and typed static adjustments."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from .card_program_faces import program_matches_face
from .characteristic_fragments import CharacteristicQuantityReduction
from .continuous_effects import Layer
from .object_query import object_query_result
from .public_quantity_reductions import reduce_public_quantity
from .semantic_runtime.component_registry import RuntimeComponentRegistry, exact_fields
from .semantic_runtime.context import SemanticNodeError


DEVOTION_CAPABILITY = 'state_query.devotion.current'
DEVOTION_MODIFIER_HANDLER = 'rule.devotion.fixed-increase.v1'
DEVOTION_EVENT = 'devotion.evaluate'


@dataclass(frozen=True, slots=True)
class FixedDevotionIncreaseHandler:
    handler_id: str = DEVOTION_MODIFIER_HANDLER
    schema_version: int = 1
    family: str = 'state_query.devotion.fixed_increase'
    event: str = DEVOTION_EVENT
    rule_references: tuple[str,...] = ('700.5','700.5a','611.3a','611.3b')
    capability_dependencies: tuple[str,...] = (DEVOTION_CAPABILITY,)

    def validate(self, descriptor: Mapping[str,Any]) -> int:
        exact_fields(descriptor,{'handler_id','schema_version','event','amount'},field='Fixed devotion increase')
        if (descriptor['handler_id']!=self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version']!=1 or descriptor['event']!=self.event
                or type(descriptor['amount']) is not int or descriptor['amount']<1):
            raise SemanticNodeError('Fixed devotion increase descriptor is malformed')
        return descriptor['amount']

    def lower(self, descriptor, context):
        return (self.validate(descriptor),)


@lru_cache(maxsize=1)
def default_devotion_modifier_registry():
    from .rules.capabilities import load_default_capability_registry
    registry=RuntimeComponentRegistry((FixedDevotionIncreaseHandler(),))
    registry.require_registered_capabilities(load_default_capability_registry())
    return registry.freeze()


def _static_devotion_increase(host,seat):
    amount=0
    component_map=None
    registry=default_devotion_modifier_registry()
    for source in host.state.cards.values():
        if source.zone!='battlefield' or source.controller!=seat or source.phased_out:
            continue
        record=host.card_record(source)
        if record is None:continue
        for program in host.semantics.runtime_handler_programs_for_oracle(record.oracle_id,
                active_zone='battlefield',event=DEVOTION_EVENT):
            if not host.semantic_program_is_current_trusted(program) or not program_matches_face(record,program,source):
                continue
            if component_map is None:component_map=host._effective_static_component_key_map()
            if program.key not in component_map.get(source.object_id,()):continue
            for descriptor in program.handlers:
                if descriptor.get('handler_id')==DEVOTION_MODIFIER_HANDLER:
                    amount+=registry.lower(descriptor,None)[0]
    return amount


def current_devotion(host,seat,colors,*,include_modifiers=True,_enforce_static_component_applicability=True):
    colors=tuple(colors)
    if len(colors) not in {1,2} or tuple(sorted(set(colors)))!=colors or any(c not in 'WUBRG' for c in colors):
        raise ValueError('Devotion requires one or two distinct canonical mana colors')
    rows=[]
    for source in host.state.cards.values():
        if source.zone!='battlefield' or source.controller!=seat or source.phased_out:continue
        data=host._effective_card_data(source,maximum_layer=Layer.TEXT,
            _enforce_static_component_applicability=_enforce_static_component_applicability)
        row=object_query_result(source,{**data,'mana_value':0},type_parts=host._type_parts(str(data.get('type_line') or '')),
            known_to_actor=True,attached_to_ref=None)
        rows.append((row,data,source.object_kind))
    base=reduce_public_quantity(rows,CharacteristicQuantityReduction.DEVOTION,mana_colors=colors)
    return base+(_static_devotion_increase(host,seat) if include_modifiers else 0)
