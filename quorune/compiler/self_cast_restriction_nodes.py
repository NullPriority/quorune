from __future__ import annotations

"""Complete intrinsic prior-spell and current Snow-land casting clauses."""

import re

from ..cast_timing import CAST_PERMISSION_ACTIVE_ZONE
from ..semantic_runtime.self_cast_restrictions import SelfCastRestrictionHandler
from .public_state_queries import fixed_public_state_condition
from .static_runtime_nodes import runtime_handler_node


def self_cast_restriction_nodes(*, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals):
    combat = re.fullmatch(r"Cast this spell only during the declare attackers step"
        r"(?P<attacked> and only if you've been attacked this step)?\.?",
        ' '.join(material_line.strip().split()), re.I)
    if combat is not None:
        from ..declare_attack_cast_model import DeclareAttackCastSpec
        from ..semantic_runtime.declare_attack_cast import DeclareAttackCastRestrictionHandler
        handler = DeclareAttackCastRestrictionHandler()
        descriptor = {'handler_id': handler.handler_id, 'schema_version': 1,
            'event': handler.event, 'restriction': DeclareAttackCastSpec(bool(combat['attacked'])).to_dict()}
        handler.validate(descriptor)
        return (runtime_handler_node(node_id=node_id, line=line, span=span,
            compiled=('declare-attackers-cast-restriction-v1', descriptor, handler.capability_dependencies),
            kind='static_ability', event=handler.event, active_zone=CAST_PERMISSION_ACTIVE_ZONE,
            dependency_reason='Printed combat casting requires its declared-player occurrence and shared cast owner',
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals),)
    match = re.fullmatch(r"Cast this spell only if (?P<condition>you've cast another spell this turn|you control a snow land)\.?",
        ' '.join(material_line.strip().split()), re.I)
    if match is None:
        return None
    condition = fixed_public_state_condition(
        match['condition'].casefold().replace('another spell', 'a spell'), source_name=source_name)
    if condition is None:
        return None
    handler = SelfCastRestrictionHandler()
    descriptor = {'handler_id': handler.handler_id, 'schema_version': handler.schema_version,
        'event': handler.event, 'source_condition': condition.to_dict()}
    handler.validate(descriptor)
    return (runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('fixed-self-cast-restriction-v1', descriptor, handler.capability_dependencies),
        kind='static_ability', event=handler.event, active_zone=CAST_PERMISSION_ACTIVE_ZONE,
        dependency_reason='Intrinsic cast restrictions require their public-condition and proposal owners',
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals),)
