from __future__ import annotations

"""Full-consumption fixed creature Goad over existing target and set queries."""

from dataclasses import replace
import re

from .direct_target import direct_permanent_target_spec
from .public_state_queries import fixed_characteristic_battlefield_query_subject
from .fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
from ..goad_instruction import GOAD_MECHANIC, GOAD_CAPABILITY

_PLAYER_SCHEMA={'zones':['player'],'categories':['player'],'player_relation':'any','count':1}


def fixed_goad_effect_template(text):
    match=re.fullmatch(r'Goad (?P<subject>.+?)\.?',text.strip(),re.I)
    if match is None:return None
    subject=match['subject'].rstrip('.');target=direct_permanent_target_spec(subject)
    if subject.casefold()=='each creature target player controls':
        from ..object_predicate import ObjectQuerySpec
        query=ObjectQuerySpec(zones=('battlefield',),types_all=('creature',),controller='$target.0')
        return ('fixed-goad-player-set-v2',({'op':'goad','schema_version':2,'predicate':query.to_dict()},),
                dict(_PLAYER_SCHEMA),(GOAD_MECHANIC,'cr-115-targets'))
    if target is not None:
        if 'creature' not in target.types_all and target.types_any != ('creature',):return None
        if subject.casefold() in {'target creature','target creature an opponent controls'}:return None
        return ('fixed-goad-target-v2',({'op':'goad','schema_version':2,'card':'$target.0'},),
                target.to_target_schema(),(GOAD_MECHANIC,'cr-115-targets'))
    subject=re.sub(r'^all ','',subject,flags=re.I)
    if subject.casefold()=='creatures you don\'t control':subject='Creatures your opponents control'
    parsed=fixed_characteristic_battlefield_query_subject(subject)
    if parsed is None:return None
    relation,query,excluded=parsed
    if 'creature' not in query.types_all:return None
    if relation=='source_controller':query=replace(query,controller='$controller')
    elif relation=='source_opponents':query=replace(query,excluded_controllers=('$controller',))
    elif relation!='any':return None
    if excluded:query=replace(query,exclude_ref='$source')
    if not fixed_resolution_characteristic_query_is_closed(query,target_schema=None):return None
    return ('fixed-goad-public-set-v2',({'op':'goad','schema_version':2,'predicate':query.to_dict()},),None,(GOAD_MECHANIC,))
