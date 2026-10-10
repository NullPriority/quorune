from __future__ import annotations

"""Source-spanned fixed player hexproof and shroud productions."""

from ..semantic_runtime.player_target_protection import PLAYER_TARGET_PROTECTION_HANDLER_ID,PLAYER_TARGET_PROTECTION_CAPABILITY_ID
from .static_runtime_nodes import runtime_handler_node


def player_target_protection_node(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    normalized=material_line.casefold().rstrip('.')
    if normalized not in {'you have hexproof','you have shroud'}:return None
    keyword=normalized.rsplit(' ',1)[1]
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('controller-player-target-protection-v1',{'handler_id':PLAYER_TARGET_PROTECTION_HANDLER_ID,'schema_version':1,'event':'characteristics.evaluate','keyword':keyword},PLAYER_TARGET_PROTECTION_CAPABILITY_ID),
        kind='static_ability',event='characteristics.evaluate',dependency_reason='Player protection requires current trusted source components and shared target legality.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
