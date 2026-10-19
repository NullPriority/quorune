from __future__ import annotations

"""Closed spell taxes and source-face fixed generic target reductions."""

import re
from ..targeted_spell_costs import TargetedSpellCostSpec,TargetedCostSubject,TARGETED_SPELL_COST_HANDLER,TARGETED_SPELL_COST_EVENT,TARGETED_SPELL_COST_CAPABILITY
from ..rules.source_references import SourceReferenceSpec
from ..targeted_spell_costs import SELF_TARGET_REDUCTION_CAPABILITY
from ..replacement.immutable import FrozenMap
from .direct_target import direct_permanent_target_spec,stack_target_schema


def _self_reduction_target_schema(subject):
    subject=' '.join(subject.casefold().split())
    direct=direct_permanent_target_spec('target '+subject)
    if direct is not None:return direct.to_target_schema()
    graveyard=re.fullmatch(r'creature card with mana value [0-9]+ or less',subject,re.I)
    if graveyard is not None:
        direct=direct_permanent_target_spec('target '+subject.replace(' card','',1))
        return {**direct.to_target_schema(),'zones':['graveyard'],'categories':['card']} if direct is not None else None
    if subject.casefold()=='creature spell':return stack_target_schema(categories=('spell',),types_any=('creature',))
    if subject.casefold()=='blue spell':return stack_target_schema(categories=('spell',),colors_any=('U',))
    return None


def self_target_reduction_template(text):
    match=re.fullmatch(r'This spell costs \{(?P<amount>[1-9]\d*)\} less to cast if it targets (?P<subject>.+?)\.?',text.strip(),re.I)
    if match is None:return None
    subject=re.sub(r'^(?:a|an) ','',match['subject'],flags=re.I)
    schema=_self_reduction_target_schema(subject)
    if schema is None:return None
    spec=TargetedSpellCostSpec(TargetedCostSubject.SPELL_TARGET,-int(match['amount']),2,FrozenMap(schema))
    return 'self-target-fixed-generic-reduction-v2',{'handler_id':TARGETED_SPELL_COST_HANDLER,'schema_version':1,
        'event':TARGETED_SPELL_COST_EVENT,'modifier':spec.to_dict()},(TARGETED_SPELL_COST_CAPABILITY,SELF_TARGET_REDUCTION_CAPABILITY)


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
