"""Compiler support for prevention over one closed current recipient scope."""
from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype_surface
from ..received_damage_model import FIXED_DAMAGE_REMAINDER_CAPABILITY, FixedDamageRemainderScope


_PREVENTION = re.compile(
    r"If a source would deal damage to (?P<recipient>.+), prevent all but (?:1|one) of that damage\.?",
    re.IGNORECASE,
)
_CONTROLLED_SUBTYPE = re.compile(
    r"(?P<relation>another|you or a) (?P<subtype>[^,.]+) you control",
    re.IGNORECASE,
)


def fixed_remainder_prevention_template(text):
    match = _PREVENTION.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    recipient = match["recipient"]
    if recipient.casefold() == "you or a planeswalker you control":
        scope = FixedDamageRemainderScope("controller_or_planeswalker")
    else:
        creature = _CONTROLLED_SUBTYPE.fullmatch(recipient)
        if creature is None:
            return None
        subtype = canonical_creature_subtype_surface(creature["subtype"])
        if subtype is None:
            return None
        kind = "other_controlled_subtype" if creature["relation"].casefold() == "another" else "controller_or_controlled_subtype"
        scope = FixedDamageRemainderScope(kind, subtype)
    return "static-fixed-damage-remainder-v1", scope.descriptor(), FIXED_DAMAGE_REMAINDER_CAPABILITY
