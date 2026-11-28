from __future__ import annotations

"""Fixed this-turn prohibitions over ordinary proposed spell characteristics."""

from . import regex_working_set as re
from ..temporary_cast_rule_model import OPERATION,MECHANIC,TemporaryCastRestrictionSpec,opponents_nonhand_until_next_turn_effect


def temporary_cast_restriction_template(text):
    if " ".join(text.split()).casefold() == "until your next turn, your opponents can't cast spells from anywhere other than their hands.":
        return "temporary-opponents-nonhand-cast-prohibition-v2", (opponents_nonhand_until_next_turn_effect(),), None, (MECHANIC,)
    match=re.fullmatch(r"(?P<who>Your opponents|Players|Target player|Target opponent) can't cast (?P<kind>creature |noncreature )?spells (?:this turn|until end of turn)\.?",text.strip(),re.I)
    if match is None:return None
    who=match['who'].casefold();targeted=who.startswith('target')
    scope='target' if targeted else 'all' if who=='players' else 'opponents'
    restriction=TemporaryCastRestrictionSpec(scope,(match['kind'] or 'any').strip().casefold())
    schema={'zones':['player'],'categories':['player'],'count':1,'player_relation':'opponent' if who=='target opponent' else 'any'} if targeted else None
    return ('temporary-cast-prohibition-v1',({'op':OPERATION,'actor':'$controller','restriction':restriction.to_dict(),
        'player':'$target.0' if targeted else None},),schema,(MECHANIC,* (('cr-115-targets',) if targeted else ())))



def attacked_defender_cast_restriction(binding, body):
    """Bind the one unqualified source attack to its original defending player."""
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("creature.attacks", "source_attacks", "battlefield")
        or binding.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
        or body != binding.body
        or re.fullmatch(r"Defending player can't cast spells this turn[.]?", body, re.I) is None
    ):
        return None
    restriction = TemporaryCastRestrictionSpec("target")
    effect = {
        "op": OPERATION, "actor": "$controller", "restriction": restriction.to_dict(),
        "player": "$context.defending_player",
    }
    return "temporary-attacked-defender-cast-prohibition-v1", (effect,), None, (MECHANIC,)
