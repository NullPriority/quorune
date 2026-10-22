from __future__ import annotations

from ..library_search_model import LIBRARY_SEARCH_TEMP_CAPABILITY,LIBRARY_SEARCH_TEMP_OPERATION


def temporary_library_search_node_capabilities(*,effects,target_schema,mechanic_ids,**kwargs):
    if 'temporary-library-search-prohibition' not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    return (LIBRARY_SEARCH_TEMP_CAPABILITY,) if effects[0]=={'op':LIBRARY_SEARCH_TEMP_OPERATION,'actor':'$controller'} else ()


def is_closed_library_search_prohibition_program(program):
    required=temporary_library_search_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
