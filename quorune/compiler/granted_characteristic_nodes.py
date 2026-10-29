from __future__ import annotations

"""Compile bounded static characteristic children into the existing grant owner."""

from ..ability_fragments import StaticComponentSpec, ability_fragment_to_dict
from ..rules.granted_characteristic_shapes import granted_characteristic_handlers_are_closed
from ..static_component_scope import GRANTED_STATIC_CHARACTERISTIC_CAPABILITY


def _compiled_characteristic(text, *, source_name):
    from .continuous_templates import attached_fixed_characteristics_handler, fixed_power_toughness_anthem_handler
    compiled = (attached_fixed_characteristics_handler(text, source_name=source_name)
        or fixed_power_toughness_anthem_handler(text, source_name=source_name))
    if compiled is None or not granted_characteristic_handlers_are_closed((compiled[1],)):
        return None
    return compiled


def granted_characteristic_reference_is_closed(text):
    return _compiled_characteristic(text, source_name='Granted creature') is not None


def compile_granted_characteristic_inner(**values):
    from .static_runtime_nodes import runtime_handler_node
    compiled = _compiled_characteristic(values['material_line'], source_name=values['card_name'])
    if compiled is None:
        return None
    return runtime_handler_node(node_id=values['node_id'], line=values['line'], span=values['span'],
        compiled=compiled, kind='static_ability', event='characteristics.evaluate', active_zone='battlefield',
        runtime_coverage=(), dependency_reason='Granted characteristic child requires its existing continuous owner',
        capability_registry=values['capability_registry'], capability_profile=values['capability_profile'],
        residuals=values['residuals'])


def characteristic_granted_plan(*, node, oracle_id, face_id, source_line, node_id):
    if node.event != 'characteristics.evaluate' or node.effects or not granted_characteristic_handlers_are_closed(node.handlers):
        return None
    from .attached_granted_ability_nodes import AttachedGrantedAbilityPlan, GRANTED_STATIC_ABILITY_KIND, attached_granted_program_ability_id
    ability_id = attached_granted_program_ability_id(kind=GRANTED_STATIC_ABILITY_KIND,
        face_id=face_id, line=source_line, node_id=node_id)
    key = f'{oracle_id}:{ability_id}'
    return AttachedGrantedAbilityPlan(GRANTED_STATIC_ABILITY_KIND, ability_id, key,
        ability_fragment_to_dict(StaticComponentSpec(key, 3, 'characteristics.evaluate')),
        capability_dependencies=(GRANTED_STATIC_CHARACTERISTIC_CAPABILITY,))
