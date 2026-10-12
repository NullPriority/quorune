from __future__ import annotations

"""Source-spanned reveal-or-tapped entry replacement grammar."""

import re
from ..entry_reveal import EntryRevealSpec,ENTRY_REVEAL_CAPABILITY,ENTRY_REVEAL_HANDLER
from ..object_predicate import ObjectQuerySpec
from ..creature_subtypes import canonical_creature_subtype
from ..rules.source_references import SourceReferenceSpec
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .static_runtime_nodes import runtime_handler_node


def entry_reveal_spec(text,*,source_name):
    source=rf'(?:this land|{SourceReferenceSpec(source_name).regex_pattern})'
    match=re.fullmatch(rf"As {source} enters, you may reveal (?:a|an) (?P<quality>.+?) card from your hand\. If you don['’]t, {source} enters tapped\.",text,re.I)
    if match is None:return None
    qualities=tuple(sorted(match['quality'].casefold().split(' or ')))
    if not 1<=len(qualities)<=2 or len(set(qualities))!=len(qualities):return None
    if any(value not in {'forest','island','mountain','plains','swamp'} and canonical_creature_subtype(value)!=value for value in qualities):return None
    return EntryRevealSpec(ObjectQuerySpec(zones=('hand',),owner='$actor',known_to_actor=True,subtypes_any=qualities))


def entry_reveal_node(*,node_id,line,material_line,span,source_name,capability_registry,capability_profile,residuals):
    spec=entry_reveal_spec(material_line,source_name=source_name)
    if spec is None:return None
    return runtime_handler_node(node_id=node_id,line=line,span=span,
        compiled=('intrinsic-entry-reveal-hand-or-tapped-v1',{'handler_id':ENTRY_REVEAL_HANDLER,
            'schema_version':1,'event':'zone.change','spec':spec.to_dict()},ENTRY_REVEAL_CAPABILITY),
        kind='replacement_effect',event='zone.change',active_zone='all',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Private reveal entry requires its typed hand predicate and canonical entry choice.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
