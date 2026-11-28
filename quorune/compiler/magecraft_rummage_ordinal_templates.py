"""Compile one exact magecraft rummage with second and third resolution bonuses."""
from . import regex_working_set as re
from ..replacement.immutable import FrozenMap
from ..rules.source_references import SourceReferenceSpec
from ..rules.magecraft_rummage_ordinal import CAST_COPY_CONDITION, MECHANIC, magecraft_instructions


def magecraft_rummage_ordinal_template(binding,body,*,card_name):
    if (binding.event.value!="spell.cast_or_copy" or binding.active_zone!="battlefield"
            or FrozenMap(binding.event_condition or {})!=FrozenMap(CAST_COPY_CONDITION)):
        return None
    match=re.fullmatch(r"discard a card, then draw a card\. "
        r"if this is the second time this ability has resolved this turn, "
        r"(?P<source>.+?) deals 2 damage to each opponent and each creature they control\. "
        r"if it['’]s the third time, add \{R\}\{R\}\{R\}\{R\}\.", " ".join(body.split()),re.I)
    if match is None or not SourceReferenceSpec(card_name).matches(match["source"]):
        return None
    return ("magecraft-rummage-ordinal-v1", magecraft_instructions(),None,(MECHANIC,))
