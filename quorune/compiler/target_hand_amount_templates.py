from __future__ import annotations

"""Complete public-player hand-count grammar, retaining its direct target."""

import re

from ..rules.source_references import SourceReferenceSpec
from ..scalar_effect_amount_model import ScalarAmountOrigin,ScalarEffectAmountSpec,direct_player_amount_target_is_closed


_COUNT=r'the number of cards in (?:that player[\x27’]s|their) hand'


def target_hand_count_producer(value,schema):
    if direct_player_amount_target_is_closed(schema) and re.fullmatch(_COUNT,value,re.I):
        return ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT,schema_version=4)
    return None


def normalized_target_hand_damage(text,source_name):
    source=rf'(?:{SourceReferenceSpec(source_name).regex_pattern}|this (?:artifact|creature|enchantment|permanent))'
    match=re.fullmatch(rf'(?P<source>{source}) deals damage to (?P<recipient>target (?:player|opponent)) equal to (?P<count>{_COUNT})\.',text,re.I)
    if match is None:
        return text
    # The scalar assembler adds the one final sentence terminator.
    return f"{match['source']} deals damage equal to {match['count']} to {match['recipient']}"
