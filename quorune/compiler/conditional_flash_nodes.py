from __future__ import annotations

"""One source-spanned conditional timing clause with optional real Flash."""

import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..cast_timing import CAST_PERMISSION_ACTIVE_ZONE, CAST_PERMISSION_EVENT
from ..rules.source_references import SourceReferenceSpec
from ..semantic_runtime.conditional_flash import (
    CONDITIONAL_FLASH_CAPABILITY, ConditionalFlashPermissionHandler, ConditionalFlashKeywordHandler,
)
from .public_state_queries import fixed_public_state_condition
from .static_runtime_nodes import runtime_handler_node


def conditional_self_flash_nodes(*, node_id, line, material_line, span, source_name,
        capability_registry, capability_profile, residuals):
    text=re.sub(r'\s+\([^()]*\)\.?$','',material_line.strip()).rstrip('.')
    text=re.sub(r"^[A-Za-z][A-Za-z' ]{0,80} — ",'',text)
    patterns=(
        r'If (?P<condition>.+?), you may cast this spell as though it had flash',
        r'As long as (?P<condition>.+?), you may cast this spell as though it had flash',
        r'You may cast this spell as though it had flash if (?P<condition>.+)',
    )
    match=next((value for pattern in patterns if (value:=re.fullmatch(pattern,text,re.I)) is not None),None)
    actual=False
    if match is None:
        source=SourceReferenceSpec(source_name).regex_pattern
        match=re.fullmatch(rf'(?:This creature|This spell|{source}) has flash as long as (?P<condition>.+)',text,re.I)
        actual=match is not None
    if match is None:
        return None
    condition=fixed_public_state_condition(match['condition'],source_name=source_name)
    if condition is None:
        return None
    permission=ConditionalFlashPermissionHandler()
    descriptor={'handler_id':permission.handler_id,'schema_version':1,'event':permission.event,
        'source_condition':condition.to_dict()}
    try:
        permission.validate(descriptor)
        if actual:ConditionalFlashKeywordHandler().validate({**descriptor,
            'handler_id':ConditionalFlashKeywordHandler().handler_id,'event':'characteristics.evaluate'})
    except (TypeError,ValueError):
        return None
    capabilities={CONDITIONAL_FLASH_CAPABILITY,'timing.cast.printed_flash','continuous.characteristics.fixed_public_state'}
    if condition.kind.value=='controller_creature_numeric_exists':capabilities.add('condition.creature.fixed_numeric_exists')
    def node(identity, handler, event, zone, coverage=()):
        return runtime_handler_node(node_id=identity,line=line,span=span,
            compiled=('conditional-self-flash-v1',handler,tuple(sorted(capabilities))),kind='static_ability',
            event=event,active_zone=zone,runtime_coverage=coverage,
            dependency_reason='Conditional self Flash requires current timing and public-state capability closure',
            capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
    nodes=[node(node_id,descriptor,CAST_PERMISSION_EVENT,CAST_PERMISSION_ACTIVE_ZONE)]
    if actual:
        keyword={**descriptor,'handler_id':ConditionalFlashKeywordHandler().handler_id,'event':'characteristics.evaluate'}
        nodes.extend(node(node_id+suffix,keyword,'characteristics.evaluate',zone,(CURRENT_ABILITY_FRAGMENT_COVERAGE,))
            for suffix,zone in ((':flash-keyword','battlefield'),(':flash-stack','stack')))
    return tuple(nodes)
