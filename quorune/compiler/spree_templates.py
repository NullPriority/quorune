from __future__ import annotations

"""Compile a complete Spree face through the existing closed modal owner."""

from dataclasses import dataclass
import hashlib
from . import regex_working_set as re

from ..spree_model import TIERED_VERSION, SpreeModeCosts
from ..util import mana_cost_to_vector, stable_json
from .modal_templates import fixed_nonrepeating_modal_template


@dataclass(frozen=True, slots=True)
class FixedSpreeModalTemplate:
    body: object
    prices: SpreeModeCosts

    @property
    def cost_schema(self):
        return {self.prices.field: self.prices.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        digest = hashlib.sha256(stable_json({'body': template, 'prices': self.prices.to_dict()}).encode('utf-8')).hexdigest()[:16]
        return f"fixed-{self.prices.field}-modal-{digest}-v1", effects, schema, (*mechanics, self.prices.mechanic)


_PRICE = r"(?P<cost>(?:\{(?:[0-9]+|[WUBRGC])\})+)"
_SPREE_LINE = re.compile(rf"\+ {_PRICE} \u2014 (?P<body>.+)")
_TIERED_LINE = re.compile(rf"\u2022 (?P<label>[A-Z][A-Za-z' ]*[A-Za-z']) \u2014 {_PRICE} \u2014 (?P<body>.+)")


def _fixed_priced_mode_template(material_rows, *, compile_effect, line, version):
    tiered = version == TIERED_VERSION
    header = "Choose one \u2014" if tiered else "Choose one or more \u2014"
    normalized = [(material_rows[0][0], header, material_rows[0][2])]
    prices = {}
    labels = {} if tiered else None
    for index, row in enumerate(material_rows[1:], 1):
        match = line.fullmatch(row[1])
        if match is None:
            return None
        vector, complex_symbols = mana_cost_to_vector(match["cost"])
        if complex_symbols:
            return None
        mode = f"mode_{index}"
        prices[mode] = vector
        if labels is not None:
            labels[mode] = match["label"]
        body = f"{match['label']} \u2014 {match['body']}" if tiered else match["body"]
        normalized.append((row[0], "\u2022 " + body, row[2]))
    body = fixed_nonrepeating_modal_template(normalized, compile_effect=compile_effect)
    if body is None:
        return None
    costs = SpreeModeCosts(prices, version, labels)
    costs.validate_schema(body.target_schema)
    return FixedSpreeModalTemplate(body, costs)


def fixed_spree_modal_template(material_rows, *, compile_effect):
    if len(material_rows) not in {3, 4, 5, 6} or material_rows[0][1] != "Spree":
        return None
    return _fixed_priced_mode_template(material_rows, compile_effect=compile_effect, line=_SPREE_LINE, version=1)


def fixed_tiered_modal_template(material_rows, *, compile_effect):
    if len(material_rows) not in {3, 4, 5, 6} or material_rows[0][1] not in {"Tiered", "Tiered (Choose one additional cost.)"}:
        return None
    return _fixed_priced_mode_template(
        material_rows, compile_effect=compile_effect, line=_TIERED_LINE, version=TIERED_VERSION,
    )
