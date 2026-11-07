from __future__ import annotations

"""Untargeted results on one sealed permanent occurrence incarnation."""

import re
from ..received_damage_model import RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT
from .received_damage_bindings import RECEIVED_PERMANENT_VARIANTS

OCCURRENCE_OBJECT='$event.permanent_object'


def entry_permanent_result(binding, body):
    from ..rules.entry_permanent_results import (
        ENTRY_ANIMATION_OPERATION, ENTRY_PERMANENT_RESULT_MECHANIC,
        entry_permanent_result_binding, entry_permanent_result_effect,
    )
    from ..saddle import SADDLE_OPERATION

    operation = {
        "it becomes an artifact creature until end of turn.": ENTRY_ANIMATION_OPERATION,
        "it becomes saddled until end of turn.": SADDLE_OPERATION,
    }.get(body.casefold())
    if operation is None or not entry_permanent_result_binding(binding.event.value, binding.event_condition, operation):
        return None
    return ('fixed-entry-permanent-result-v1', (entry_permanent_result_effect(operation),), None,
        (ENTRY_PERMANENT_RESULT_MECHANIC,)), True


def occurrence_object_result(binding,body,*,effect_template,card_name):
    if binding.variant == 'source_block_pair_counterpart':
        moved=re.fullmatch(r"(?P<destroy>Destroy that creature\.)(?P<prohibit> It can['’]t be regenerated\.)?|(?P<bounce>Return that creature to its owner's hand\.)",body,re.I)
        if moved is not None:
            from ..event_counter_reference import BLOCK_COUNTERPART_OBJECT
            op='bounce' if moved['bounce'] else 'destroy'
            instruction={'op':op,'card':BLOCK_COUNTERPART_OBJECT}
            if moved['prohibit']:instruction['regeneration_prohibited']=True
            return ('fixed-block-counterpart-zone-result-v1',(instruction,),None,
                ('return-to-owner-hand',) if op=='bounce' else ('destroy',*(('regeneration-prohibition',) if moved['prohibit'] else ()))),False
        damage = re.fullmatch(r'this creature deals (?P<amount>[1-9][0-9]*) damage to that creature\.', body, re.I)
        if damage is not None:
            from .damage_templates import source_pronoun_damage_effect_template
            from ..event_counter_reference import BLOCK_COUNTERPART_OBJECT
            leaf = source_pronoun_damage_effect_template('It deals ' + damage['amount'] + ' damage to target creature.')
            if leaf is None:
                return None
            template, effects, schema, mechanics = leaf.compiled()
            return (template, ({**effects[0], 'target': BLOCK_COUNTERPART_OBJECT},), None,
                    tuple(mechanic for mechanic in mechanics if mechanic != 'cr-115-targets')), True
        from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
        match = re.fullmatch(r'that creature gains (?P<keyword>[a-z ]+) until end of turn\.', body, re.I)
        keyword = match['keyword'].title() if match is not None else None
        if keyword not in FIXED_CHARACTERISTIC_KEYWORDS:
            return None
        from ..event_counter_reference import BLOCK_COUNTERPART_OBJECT
        template, effects, schema, mechanics = FixedSourceCharacteristicsTemplate(
            source_kind='block-counterpart', keywords=(keyword,)).compiled()
        return (template, ({**effects[0], 'card': BLOCK_COUNTERPART_OBJECT},), None, mechanics), True
    tap = binding.event.value in {'permanent.tap','permanent.untap'} and binding.variant in {'public_tap_state_transition','source_public_tap_state_transition'}
    receiving = binding.variant in RECEIVED_PERMANENT_VARIANTS and binding.event.value in {RECEIVED_DAMAGE_EVENT,RECEIVED_COMBAT_DAMAGE_EVENT,'permanent.tap'}
    if not tap and not receiving:return None
    if tap and re.fullmatch(r'its controller gets a poison counter\.',body,re.I):
        return ('poison-fixed-occurrence-controller-v1',({'op':'place_player_counters','subjects':'target','counter':'poison',
            'amount':1,'source':'$source','target':{'kind':'occurrence_controller_information','schema_version':1}},),
            None,('cr-122-counters',)),True
    match=re.fullmatch(r'Destroy (?:it|that permanent|that creature|that artifact|that land)\.(?P<prohibit> It can[\x27’]t be regenerated\.)?',body,re.I)
    if match is None:return None
    prohibit=bool(match['prohibit'])
    compiled=effect_template('Destroy target permanent.'+(" It can't be regenerated." if prohibit else ''),card_name=card_name)
    expected={'op':'destroy','card':'$target.0',**({'regeneration_prohibited':True} if prohibit else {})}
    if compiled[0] is None or len(compiled[1])!=1 or compiled[1][0]!=expected:return None
    return ('destroy-fixed-occurrence-object-v1',({**expected,'card':OCCURRENCE_OBJECT},),None,tuple(m for m in compiled[3] if m!='cr-115-targets')),True
