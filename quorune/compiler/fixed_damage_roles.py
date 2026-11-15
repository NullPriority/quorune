"""One simultaneous instruction with independently constrained damage roles."""
from copy import deepcopy
import re

from ..fixed_damage_role_model import FixedDamageRoleSpec,FIXED_DAMAGE_ROLE_FIELD,FIXED_DAMAGE_ROLE_MECHANIC
from .damage_templates import FixedDamageEffectTemplate,fixed_damage_effect_template,source_pronoun_damage_effect_template


def fixed_damage_role_template(text,*,card_name,source_is_permanent):
    material=' '.join(text.strip().split())
    match=re.fullmatch(r'(?P<source>.+?) deals (?P<body>.+)\.',material,re.I)
    if match is None:return None
    parts=re.split(r',\s*(?:and\s+)?(?=[1-9][0-9]* damage to )|\s+and\s+(?=[1-9][0-9]* damage to )',match['body'],flags=re.I)
    if not 2<=len(parts)<=4:return None
    groups=[];amounts=[];minimums=[];maximums=[];mechanics={FIXED_DAMAGE_ROLE_MECHANIC,'cr-120-damage','cr-115-targets'}
    for index,part in enumerate(parts):
        clause=re.fullmatch(r'(?P<amount>[1-9][0-9]*) damage to (?P<recipient>.+)',part,re.I)
        if clause is None:return None
        recipient=clause['recipient'];distinct=False;minimum=maximum=1
        if (recipient.casefold()=='a third target' and index!=2
                or recipient.casefold()=='a fourth target' and index!=3):return None
        optional=re.fullmatch(r'up to one (?P<other>other )?target (?P<body>.+)',recipient,re.I)
        if optional:
            if not index:return None
            recipient='target '+optional['body'];distinct=bool(optional['other']);minimum=0
        if re.fullmatch(r'each of up to two other targets',recipient,re.I):
            if not index:return None
            recipient='any target';distinct=True;minimum=0;maximum=2
        if recipient.casefold() in {'any other target','another target','a third target','a fourth target'}:
            if not index:return None
            recipient='any target';distinct=True
        elif 'other target' in recipient.casefold():return None
        source=f"{match['source']} deals {clause['amount']} damage to {recipient}."
        child=fixed_damage_effect_template(source,card_name=card_name)
        if child is None and source_is_permanent is True:child=source_pronoun_damage_effect_template(source)
        if (not isinstance(child,FixedDamageEffectTemplate) or child.target_schema is None
                or child.effects[0].get('op')!='damage' or child.effects[0].get('target')!='$target.0'):
            return None
        group=deepcopy(dict(child.target_schema))
        if group.get('count')!=1:return None
        if minimum==0:group.pop('count');group.update(min=minimum,max=maximum)
        group['id']='damage_role_'+str(index)
        if distinct:group['different_from_groups']=[g['id'] for g in groups]
        groups.append(group);amounts.append(child.amount);minimums.append(minimum);maximums.append(maximum);mechanics.update(child.mechanics)
    spec=FixedDamageRoleSpec(tuple(amounts),tuple(minimums),tuple(maximums))
    return ('fixed-target-role-damage-v1',({'op':'damage_fixed_set','source':'$source','amount':spec.total,
        FIXED_DAMAGE_ROLE_FIELD:spec.to_dict()},),{'groups':groups},tuple(sorted(mechanics)))
