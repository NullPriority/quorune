from __future__ import annotations

"""Whole instructions gated by immutable actual spell-payment counts."""

from copy import deepcopy
import re

from ..spell_mana_conditions import ManaSpentCondition,PAID_MANA_CONDITION_MECHANIC
from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC,RESOLUTION_CONDITION_OPERATION
from .fixed_numbers import FIXED_COUNT_PATTERN,fixed_number
from .kicked_same_target_replacements import same_target_kicked_replacement
from .public_self_replacement_templates import _alternative_body
from ..target_controller_information import target_controller_reference,TARGET_CONTROLLER_INFORMATION_MECHANIC
from ..rules.source_references import SourceReferenceSpec


def paid_mana_resolution_template(text,*,source_name,compile_component):
    text=' '.join(text.strip().split())
    match=re.fullmatch(rf'(?P<prefix>.*?)(?:[A-Za-z][A-Za-z0-9\x27 ]{{0,80}} — )?'
        rf'If at least (?P<count>{FIXED_COUNT_PATTERN}) (?P<type>white|blue|black|red|green|colorless) mana '
        rf'was spent to cast this spell, (?P<body>.+)',text,re.I)
    if match is None:return None
    prefix=match['prefix'].strip();body=match['body'].strip();minimum=fixed_number(match['count'])
    if minimum<1 or prefix and not prefix.endswith('.') or not body.endswith('.'):return None
    condition=ManaSpentCondition({'white':'W','blue':'U','black':'B','red':'R','green':'G','colorless':'C'}[match['type'].casefold()],minimum)
    replacing=bool(re.search(r'\binstead\b',body,re.I))
    mandatory=compile_component(prefix) if prefix else (None,(),None,())
    if prefix and mandatory[0] is None:return None
    if replacing:
        if not prefix or len(mandatory[1])!=1 or mandatory[2] is None:return None
        alternative=_alternative_body(prefix,body)
        if alternative is None:return None
        conditional=same_target_kicked_replacement(prefix,alternative,mandatory,compile_component)
        if conditional is None:return None
        prefix_effects=();prefix_ids=();schema=mandatory[2];otherwise=mandatory
    else:
        if re.search(r'\b(?:if|otherwise|this way|instead)\b',body,re.I):return None
        conditional=compile_component(body)
        if conditional[0] is None:
            recipient=re.fullmatch(rf'(?P<source>.+?) deals (?P<amount>{FIXED_COUNT_PATTERN}) damage to that creature[\x27’]s controller\.',body,re.I)
            if (recipient is not None and SourceReferenceSpec(source_name).matches(recipient['source'])
                    and len(mandatory[1])==1 and mandatory[1][0].get('op')=='damage'
                    and mandatory[2] is not None and mandatory[2].get('types_any')==['creature']
                    and mandatory[2].get('count')==1):
                conditional=('fixed-target-controller-damage-v1',({'op':'damage','source':'$source',
                    'target':target_controller_reference(),'amount':fixed_number(recipient['amount'])},),None,
                    ('cr-120-damage',TARGET_CONTROLLER_INFORMATION_MECHANIC))
        if conditional[0] is None or conditional[2] is not None:return None
        prefix_effects=mandatory[1];prefix_ids=mandatory[3];schema=mandatory[2];otherwise=None
    if not conditional[1] or len(prefix_effects)+len(conditional[1])>8:return None
    wrapper={'op':RESOLUTION_CONDITION_OPERATION,'schema_version':7 if replacing else 6,'player':'$controller',
        'condition':condition.to_dict(),'cast_fact':condition.reference(),'effects':deepcopy(list(conditional[1])),
        'mechanic_ids':list(conditional[3]),'prefix_mechanic_ids':list(prefix_ids)}
    if otherwise is not None:wrapper.update(otherwise_effects=deepcopy(list(otherwise[1])),otherwise_mechanic_ids=list(otherwise[3]))
    from ..resolution_conditions import validate_resolution_condition_instruction
    try:validate_resolution_condition_instruction(wrapper)
    except (TypeError,ValueError):return None
    mechanics=tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC,PAID_MANA_CONDITION_MECHANIC,
        *prefix_ids,*conditional[3],*(otherwise[3] if otherwise else ()))))
    return ('paid-mana-same-target-replacement-v1' if replacing else 'paid-mana-resolution-condition-v1',
        (*deepcopy(prefix_effects),wrapper),deepcopy(schema),mechanics)
