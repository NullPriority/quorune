from __future__ import annotations

"""Whole ordinary external entry-counter clauses, without runtime source text."""

import re
from ..external_entry_counter_model import EXTERNAL_ENTRY_COUNTER_CAPABILITY,EXTERNAL_ENTRY_COUNTER_HANDLER,ExternalEntryCounterSpec
from ..rules.source_references import SourceReferenceSpec
from .creature_subtypes import canonical_creature_subtype


def external_entry_counter_handler(text,*,source_name):
    material=' '.join(text.strip().split()).rstrip('.')
    source=SourceReferenceSpec(source_name).regex_pattern
    fixed=re.fullmatch(r'Each other (?P<kind>non-[A-Za-z-]+ |[A-Za-z-]+ )?creature you control enters with an additional \+1/\+1 counter on it',material,re.I)
    if fixed is not None:
        kind=(fixed['kind'] or '').strip();required=excluded=None
        if kind:
            negative=kind.casefold().startswith('non-');subtype=canonical_creature_subtype(kind[4:] if negative else kind)
            if subtype is None:return None
            if negative:excluded=subtype
            else:required=subtype
        spec=ExternalEntryCounterSpec(required_subtype=required,excluded_subtype=excluded)
    else:
        stat=re.fullmatch(rf"Each other creature you control enters with a number of additional \+1/\+1 counters on it equal to (?:this creature['’]s|{source}['’]s) (?P<stat>power|toughness)(?P<mutant> and as a Mutant in addition to its other types)?",material,re.I)
        counters=re.fullmatch(r'Each other creature you control enters with an additional X \+1/\+1 counters on it, where X is the number of \+1/\+1 counters on this creature',material,re.I)
        if stat is not None:
            try:spec=ExternalEntryCounterSpec(amount_source='source_'+stat['stat'].casefold(),add_mutant=bool(stat['mutant']))
            except ValueError:return None
        elif counters is not None:spec=ExternalEntryCounterSpec(amount_source='source_plus_one_counters')
        else:return None
    return ('external-additional-entry-counter-v1',{'handler_id':EXTERNAL_ENTRY_COUNTER_HANDLER,'schema_version':1,
        'event':'zone.change','entry_counter':spec.to_dict()},(EXTERNAL_ENTRY_COUNTER_CAPABILITY,'counter.placement.quantity_replacement'))


def external_entry_counter_node(*,node_id,line,material_line,span,source_name,
        capability_registry,capability_profile,residuals):
    compiled=external_entry_counter_handler(material_line,source_name=source_name)
    if compiled is None:return None
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from .static_runtime_nodes import runtime_handler_node
    return runtime_handler_node(node_id=node_id,line=line,span=span,compiled=compiled,
        kind='static_ability',event='zone.change',active_zone='battlefield',
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        dependency_reason='Additional entry counters require their current-source prospective replacement owner.',
        capability_registry=capability_registry,capability_profile=capability_profile,residuals=residuals)
