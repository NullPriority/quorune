from __future__ import annotations
from ..semantic_runtime.static_cast_rules import SPLIT_SECOND_HANDLER_ID,SPLIT_SECOND_CAPABILITY,STATIC_CAST_RULE_EVENT
from .static_runtime_nodes import runtime_handler_node


def split_second_keyword_node(*,node_id,line,span,mechanics,capability_registry,capability_profile,residuals):
    if mechanics!=('split second',):return None
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('split-second-stack-static-v1',{'handler_id':SPLIT_SECOND_HANDLER_ID,'schema_version':1,'event':STATIC_CAST_RULE_EVENT},(SPLIT_SECOND_CAPABILITY,)),
        kind='keyword_ability',event=STATIC_CAST_RULE_EVENT,active_zone='stack',
        dependency_reason='Split second requires trusted stack priority restrictions',capability_registry=capability_registry,
        capability_profile=capability_profile,residuals=residuals)
