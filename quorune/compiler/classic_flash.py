from __future__ import annotations

"""One intrinsic instant-timing permission with its printed cleanup consequence."""

from ..cast_timing import CLASSIC_FLASH_CAPABILITY,CLASSIC_FLASH_HANDLER,CastTimingPermission
from .static_runtime_nodes import runtime_handler_node

_LINE="You may cast this spell as though it had flash. If you cast it any time a sorcery couldn't have been cast, the controller of the permanent it becomes sacrifices it at the beginning of the next cleanup step."


def classic_flash_node(*,node_id,line,material_line,span,capability_registry,capability_profile,residuals):
    if material_line.strip().casefold()!=_LINE.casefold():return None
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('classic-flash-next-cleanup-v1',{'handler_id':CLASSIC_FLASH_HANDLER,'schema_version':1,
            'event':'cast.permission','permission':CastTimingPermission().to_dict()},CLASSIC_FLASH_CAPABILITY),
        kind='static_ability',event='cast.permission',active_zone='playable',runtime_coverage=('classic_flash_permission',),
        dependency_reason='Intrinsic instant timing and its once-only next-cleanup sacrifice require their shared permission owner.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
