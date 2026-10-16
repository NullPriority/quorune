from __future__ import annotations

"""Compile a complete Spree face through the existing closed modal owner."""

from dataclasses import dataclass
import hashlib
import re

from ..spree_model import SPREE_FIXED_MECHANIC, SpreeModeCosts
from ..util import mana_cost_to_vector, stable_json
from .modal_templates import fixed_nonrepeating_modal_template


@dataclass(frozen=True, slots=True)
class FixedSpreeModalTemplate:
    body: object
    prices: SpreeModeCosts

    @property
    def cost_schema(self):
        return {'spree': self.prices.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        digest = hashlib.sha256(stable_json({'body': template, 'prices': self.prices.to_dict()}).encode('utf-8')).hexdigest()[:16]
        return 'fixed-spree-modal-' + digest + '-v1', effects, schema, (*mechanics, SPREE_FIXED_MECHANIC)


def fixed_spree_modal_template(material_rows, *, compile_effect):
    if len(material_rows) not in {3, 4, 5, 6} or material_rows[0][1] != 'Spree':
        return None
    normalized = [(material_rows[0][0], 'Choose one or more \u2014', material_rows[0][2])]
    prices = {}
    for index, row in enumerate(material_rows[1:], 1):
        match = re.fullmatch(r'\+ (?P<cost>(?:\{(?:[0-9]+|[WUBRGC])\})+) \u2014 (?P<body>.+)', row[1])
        if match is None:
            return None
        vector, complex_symbols = mana_cost_to_vector(match['cost'])
        if complex_symbols:
            return None
        prices['mode_' + str(index)] = vector
        normalized.append((row[0], '\u2022 ' + match['body'], row[2]))
    body = fixed_nonrepeating_modal_template(normalized, compile_effect=compile_effect)
    if body is None:
        return None
    costs = SpreeModeCosts(prices)
    costs.validate_schema(body.target_schema)
    return FixedSpreeModalTemplate(body, costs)
