from __future__ import annotations

"""Typed equality for closed fixed own-library search-to-battlefield results."""

from collections.abc import Mapping
from ..library_search_model import FixedCountedLibrarySearchTemplate
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.library_search_capability_shapes import fixed_library_search_node_capabilities


def _fixed_battlefield_search(effect):
    required={'op','zone','selector','count','destination','shuffle_after','enters_tapped_override'}
    optional={'searching_player','reveal'}
    if (not isinstance(effect,Mapping) or not required<=set(effect) or set(effect)-required-optional
            or effect['op']!='search' or effect['zone']!='library' or effect['destination']!='battlefield'
            or effect['shuffle_after'] is not True or effect['enters_tapped_override'] is not True
            or effect.get('searching_player','$controller')!='$controller'
            or effect.get('reveal',False) is not False):return None
    count=effect['count']
    if (not isinstance(count,Mapping) or set(count)!={'minimum','maximum'}
            or any(type(count[k]) is not int or count[k]!=1 for k in count)):return None
    selector=effect['selector'];fields={'types':'types_all','types_any':'types_any','subtypes_any':'subtypes_any','supertypes':'supertypes_all','colors_any':'colors_any'}
    if not isinstance(selector,Mapping) or not selector or set(selector)-set(fields):return None
    if any(not isinstance(v,(list,tuple)) or any(type(x) is not str for x in v) for v in selector.values()):return None
    try:
        query=ObjectQuerySpec(zones=('library',),**{fields[k]:tuple(v) for k,v in selector.items()})
        normalized={k:list(getattr(query,fields[k])) for k in selector}
        return FixedCountedLibrarySearchTemplate(count=1,optional_count=False,
            selector=FrozenMap(normalized),destination='battlefield',reveal=False,enters_tapped=True)
    except (TypeError,ValueError):return None


def fixed_battlefield_search_results_equal(current,old):
    if len(current.effects)!=1 or len(old.effects)!=1:return False
    required=fixed_library_search_node_capabilities(effects=current.effects,
        target_schema=current.target_schema,mechanic_ids=current.coverage)
    if not required or not set(required)<=set(current.capability_dependencies):return False
    a,b=(_fixed_battlefield_search(p.effects[0]) for p in (current,old))
    return a is not None and b is not None and a==b
