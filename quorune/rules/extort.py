from __future__ import annotations
from typing import Mapping

EXTORT_OPERATION='extort'
EXTORT_CAPABILITY='trigger.keyword.extort'


def extort_node_capabilities(*,effects,target_schema,mechanic_ids,cost_schema=None):
    if 'extort' not in mechanic_ids or target_schema is not None or cost_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if not isinstance(effect,Mapping) or dict(effect)!={'op':EXTORT_OPERATION,'schema_version':1,'player':'$controller','source':'$source','stage':'pay'}:
        return ()
    if type(effect['schema_version']) is not int:return ()
    return (EXTORT_CAPABILITY,)
