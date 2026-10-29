"""Bind a complete attacker-pronoun characteristic instruction to its occurrence."""

import re

from ..qualified_attack_model import QUALIFIED_ATTACK_VARIANT


def qualified_attack_characteristic_result(binding,body,*,effect_template,card_name):
    if binding.variant!=QUALIFIED_ATTACK_VARIANT and not (
            binding.event.value=='creature.attacks' and binding.variant.startswith('public_attack_')):return None
    match=re.fullmatch(r'(?:it|that creature|that permanent) (?P<body>.+)',body,re.I)
    if match is None:return None
    compiled=effect_template('This permanent '+match['body'],card_name=card_name)
    if (compiled[0] is not None and compiled[2] is None and len(compiled[1])==1
            and compiled[1][0].get('card') in {'$source','$source.zone_object'}):
        effects=({**compiled[1][0],'card':'$event.permanent_object'},)
        from ..rules.attack_event_results import attack_event_characteristic_capabilities
        if attack_event_characteristic_capabilities(effects=effects,target_schema=None,mechanic_ids=compiled[3]):
            return ('fixed-attack-occurrence-characteristics-v1',effects,None,compiled[3]),True
    return (None,(),None,()),False
