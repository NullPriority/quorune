from __future__ import annotations

"""Fixed generic ambient costs per distinct chosen target or creature."""

import re

from ..object_predicate import ObjectQuerySpec,object_query_capabilities
from ..targeted_spell_costs import (DISTINCT_TARGET_COST_CAPABILITY,TARGETED_SPELL_COST_CAPABILITY,
    TARGETED_SPELL_COST_HANDLER,TARGETED_SPELL_COST_EVENT,TargetedCostSubject,TargetedSpellCostSpec)
from .cast_cost_modifier_templates import fixed_spell_predicate


def distinct_target_cost_template(text):
    material=' '.join(text.strip().split())
    all_targets=re.fullmatch(r'Spells (?P<caster>you|your opponents) cast cost \{(?P<amount>[1-9]\d*)\} '
        r'(?P<direction>more|less) to cast for each target\.?',material,re.I)
    creatures=re.fullmatch(r'Each (?P<spells>.+?) spell you cast costs \{(?P<amount>[1-9]\d*)\} '
        r'(?P<direction>more|less) to cast for each creature it targets\.?',material,re.I)
    if all_targets is not None:
        match=all_targets;kind='all';predicate=ObjectQuerySpec()
        relation='source_controller' if match['caster'].casefold()=='you' else 'source_opponents'
    elif creatures is not None:
        match=creatures;kind='creatures';relation='source_controller'
        predicate=fixed_spell_predicate(match['spells']+' spells you cast')
        if predicate is None:return None
    else:return None
    spec=TargetedSpellCostSpec(TargetedCostSubject.DISTINCT_TARGETS,
        int(match['amount'])*(1 if match['direction'].casefold()=='more' else -1),5,
        caster_relation=relation,spell_predicate=predicate,counted_targets=kind)
    return ('distinct-target-fixed-generic-v5',{'handler_id':TARGETED_SPELL_COST_HANDLER,'schema_version':1,
        'event':TARGETED_SPELL_COST_EVENT,'modifier':spec.to_dict()},
        (TARGETED_SPELL_COST_CAPABILITY,DISTINCT_TARGET_COST_CAPABILITY,*object_query_capabilities(spec.to_dict())))
