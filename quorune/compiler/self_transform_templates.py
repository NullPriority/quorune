from __future__ import annotations

"""Source-pinned activated Transform instruction through the existing owner."""

import re
from ..rules.source_references import SourceReferenceSpec

SELF_TRANSFORM_MECHANIC='fixed-activated-source-transform'


def fixed_self_transform_effect_template(text,*,card_name):
    if re.fullmatch(r'Transform (?:this creature|this artifact|this permanent|'+SourceReferenceSpec(card_name).regex_pattern+r')\.?',text.strip(),re.I) is None:
        return None
    return ('fixed-activated-source-transform-v1',({'op':'transform','card':'$source.zone_object',
        'expected_transform_count':'$source.transform_count'},),None,(SELF_TRANSFORM_MECHANIC,'cr-701-transform'))
