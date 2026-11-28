"""Compile the complete subtype-payment and self-attack result sentence."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..rules.tap_attack_result import MECHANIC, tap_attack_effect


_PATTERN = re.compile(
    r"You may tap X untapped (?P<subtype>[A-Za-z]+) you control\. If you do, "
    r"(?P<source>.+?) gets \+X/\+0 until end of turn and deals X damage "
    r"to the player or planeswalker it['’]s attacking\.",
    re.IGNORECASE,
)


def tap_attack_result_template(binding, body, *, card_name):
    if (binding.event.value != "creature.attacks" or binding.variant != "source_attacks"
            or binding.active_zone != "battlefield" or body != binding.body):
        return None
    match = _PATTERN.fullmatch(" ".join(body.split()))
    if match is None:
        return None
    source = match["source"]
    if source_self_permanent_type(source) != "creature" and not SourceReferenceSpec(card_name).matches(source):
        return None
    subtype = canonical_creature_subtype(match["subtype"])
    if subtype is None:
        return None
    return "subtype-tap-attack-result-v1", (tap_attack_effect(subtype),), None, (MECHANIC,)
