from __future__ import annotations

"""Compile the complete colored-only optional life-cost declaration."""

import re

from ..rules.optional_life_cost_model import (
    OPTIONAL_LIFE_COST_CAPABILITY, OPTIONAL_LIFE_COST_EVENT, OPTIONAL_LIFE_COST_HANDLER,
    OptionalLifeColorReductionSpec,
)

_COLORS = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}
_DECLARATION = re.compile(
    r'^As an additional cost to cast (?P<color>white|blue|black|red|green) permanent spells, you may pay 2 life\. '
    r'Those spells cost \{(?P<symbol>[WUBRG])\} less to cast if you paid life this way\. '
    r'This effect reduces only the amount of (?P=color) mana you pay\.?$', re.IGNORECASE)


def optional_life_color_reduction_template(text):
    match = _DECLARATION.fullmatch(text.strip())
    if match is None or _COLORS[match['color'].casefold()] != match['symbol'].upper():
        return None
    spec = OptionalLifeColorReductionSpec(match['symbol'].upper())
    return ('optional-life-colored-reduction-v1',
        {'handler_id': OPTIONAL_LIFE_COST_HANDLER, 'schema_version': 1, 'event': OPTIONAL_LIFE_COST_EVENT,
            'requires_complete_card_program': True, 'cost': spec.to_dict()},
        (OPTIONAL_LIFE_COST_CAPABILITY, 'casting.additional_cost.fixed_life_payment'))
