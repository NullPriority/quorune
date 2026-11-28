"""Compile power-qualified top-library play with per-card delayed damage."""
from ..rules.power_counted_impulse import impulse_effect
from .impulse_access_templates import IMPULSE_ACCESS_MECHANIC_ID
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re

_BODY = re.compile(
    r"exile the top X cards of your library, where X is the number of creatures you control "
    r"with power (?P<power>[0-9]+) or greater\. You may play those cards until your next end step\. "
    r"At the beginning of your next end step, (?P<source>.+?) deals (?P<damage>[1-9][0-9]*) damage to "
    r"each opponent for each of those cards that are still exiled\.", re.IGNORECASE,
)


def power_counted_impulse_trigger(binding, body, *, card_name):
    if binding.variant != "source_enters_or_attacks" or binding.active_zone != "battlefield" or body != binding.body:
        return None
    match = _BODY.fullmatch(body)
    if match is None or not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    return ("power-counted-impulse-damage-v1", (impulse_effect(int(match["power"]), int(match["damage"])),),
            None, (IMPULSE_ACCESS_MECHANIC_ID,))
