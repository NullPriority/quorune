from __future__ import annotations

"""Closed source/self and one-or-more controlled-commander spell-tax grammar."""

import re
from ..targeted_spell_costs import TargetedSpellCostSpec,TargetedCostSubject,TARGETED_SPELL_COST_HANDLER,TARGETED_SPELL_COST_EVENT,TARGETED_SPELL_COST_CAPABILITY
from ..rules.source_references import SourceReferenceSpec


def targeted_spell_cost_template(text,*,source_name):
    match=re.fullmatch(r'Spells your opponents cast that target (?P<subject>.+?) cost \{(?P<amount>[1-9]\d*)\} more to cast\.?',text.strip(),re.I)
    if match is None:return None
    subject=match['subject'].strip()
    if subject.casefold()=='one or more commanders you control':kind=TargetedCostSubject.CONTROLLED_COMMANDER
    elif subject.casefold()=='this creature' or SourceReferenceSpec(source_name).matches(subject):kind=TargetedCostSubject.SOURCE
    else:return None
    spec=TargetedSpellCostSpec(kind,int(match['amount']))
    return 'targeted-fixed-spell-cost-v1',{'handler_id':TARGETED_SPELL_COST_HANDLER,'schema_version':1,
        'event':TARGETED_SPELL_COST_EVENT,'modifier':spec.to_dict()},TARGETED_SPELL_COST_CAPABILITY
