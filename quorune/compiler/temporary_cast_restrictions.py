from __future__ import annotations

"""Fixed this-turn prohibitions over ordinary proposed spell characteristics."""

import re
from ..temporary_cast_rule_model import OPERATION,MECHANIC,TemporaryCastRestrictionSpec


def temporary_cast_restriction_template(text):
    match=re.fullmatch(r"(?P<who>Your opponents|Players|Target player|Target opponent) can't cast (?P<kind>creature |noncreature )?spells (?:this turn|until end of turn)\.?",text.strip(),re.I)
    if match is None:return None
    who=match['who'].casefold();targeted=who.startswith('target')
    scope='target' if targeted else 'all' if who=='players' else 'opponents'
    restriction=TemporaryCastRestrictionSpec(scope,(match['kind'] or 'any').strip().casefold())
    schema={'zones':['player'],'categories':['player'],'count':1,'player_relation':'opponent' if who=='target opponent' else 'any'} if targeted else None
    return ('temporary-cast-prohibition-v1',({'op':OPERATION,'actor':'$controller','restriction':restriction.to_dict(),
        'player':'$target.0' if targeted else None},),schema,(MECHANIC,* (('cr-115-targets',) if targeted else ())))
