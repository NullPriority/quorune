from __future__ import annotations

"""Untargeted results on one sealed permanent occurrence incarnation."""

import re
from ..received_damage_model import RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT
from .received_damage_bindings import RECEIVED_PERMANENT_VARIANTS

OCCURRENCE_OBJECT='$event.permanent_object'


def occurrence_object_result(binding,body,*,effect_template,card_name):
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
