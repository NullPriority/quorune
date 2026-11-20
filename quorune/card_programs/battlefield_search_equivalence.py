from __future__ import annotations

"""Typed equality for closed fixed own-library search-to-battlefield results."""

from ..library_search_model import decode_fixed_tapped_battlefield_search
from ..rules.library_search_capability_shapes import fixed_library_search_node_capabilities


def fixed_battlefield_search_results_equal(current,old):
    if len(current.effects)!=1 or len(old.effects)!=1:return False
    required=fixed_library_search_node_capabilities(effects=current.effects,
        target_schema=current.target_schema,mechanic_ids=current.coverage)
    if not required or not set(required)<=set(current.capability_dependencies):return False
    a,b=(decode_fixed_tapped_battlefield_search(p.effects[0]) for p in (current,old))
    return a is not None and b is not None and a==b
