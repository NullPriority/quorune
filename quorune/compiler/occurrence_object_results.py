from __future__ import annotations

"""Untargeted results on one sealed permanent occurrence incarnation."""

import re

OCCURRENCE_OBJECT='$event.permanent_object'


def occurrence_object_result(binding,body,*,effect_template,card_name):
    if (binding.event.value not in {'permanent.tap','permanent.untap'}
            or binding.variant not in {'public_tap_state_transition','source_public_tap_state_transition'}):return None
    if re.fullmatch(r'its controller gets a poison counter\.',body,re.I):
        return ('poison-fixed-occurrence-controller-v1',({'op':'place_player_counters','subjects':'target','counter':'poison',
            'amount':1,'source':'$source','target':{'kind':'occurrence_controller_information','schema_version':1}},),
            None,('cr-122-counters',)),True
    if re.fullmatch(r'Destroy (?:it|that permanent|that creature|that artifact|that land)\.',body,re.I) is None:return None
    compiled=effect_template('Destroy target permanent.',card_name=card_name)
    if compiled[0] is None or len(compiled[1])!=1 or compiled[1][0]!={'op':'destroy','card':'$target.0'}:return None
    return ('destroy-fixed-occurrence-object-v1',({'op':'destroy','card':OCCURRENCE_OBJECT},),None,('destroy',)),True
