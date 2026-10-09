from __future__ import annotations

"""One fixed conditional result after an independently owned mandatory prefix."""

from copy import deepcopy
import re

from ..resolution_conditions import (
    RESOLUTION_CONDITION_OPERATION, RESOLUTION_CONDITION_MECHANIC,
    CAST_FACT_CONDITION_MECHANIC, KickedCastCondition,
)


def kicked_spell_condition_template(text: str, *, compile_component):
    match = re.fullmatch(r'(?P<prefix>.+?\. )?If this spell was kicked, (?P<body>.+)', text.strip(), re.I)
    if match is None:
        return None
    body = match['body']
    if re.search(r'\b(?:instead|additional|this way|otherwise|if)\b', body, re.I):
        return None
    conditional = compile_component(body)
    prefix = compile_component((match['prefix'] or '').strip()) if match['prefix'] else (None, (), None, ())
    if conditional[0] is None or not conditional[1] or conditional[2] is not None or match['prefix'] and prefix[0] is None:
        return None
    from ..resolution_conditions import cast_fact_result_is_fixed
    if not cast_fact_result_is_fixed(conditional[1], conditional[3]) or len(prefix[1]) + len(conditional[1]) > 8:
        return None
    wrapper = {
        'op': RESOLUTION_CONDITION_OPERATION, 'schema_version': 2, 'player': '$controller',
        'condition': KickedCastCondition().to_dict(), 'cast_fact': '$context.kicked',
        'effects': deepcopy(list(conditional[1])), 'mechanic_ids': list(conditional[3]),
        'prefix_mechanic_ids': list(prefix[3]),
    }
    return ('fixed-kicked-spell-condition-v1', (*prefix[1], wrapper), prefix[2],
        tuple(dict.fromkeys((RESOLUTION_CONDITION_MECHANIC, CAST_FACT_CONDITION_MECHANIC, *prefix[3], *conditional[3]))))
