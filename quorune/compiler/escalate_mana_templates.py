"""Lower one complete fixed-mana Escalate modal spell."""

from dataclasses import dataclass

from ..escalate_mana_model import ESCALATE_MANA_FIELD, EscalateManaModeSpec
from ..util import mana_cost_to_vector
from . import regex_working_set as re
from .modal_templates import FixedNonrepeatingModalTemplate, fixed_nonrepeating_modal_template


_PRICE = re.compile(r"Escalate (?P<mana>(?:\{(?:[0-9]+|[WUBRGC])\})+)\.?", re.I)


@dataclass(frozen=True, slots=True)
class FixedEscalateManaTemplate:
    body: FixedNonrepeatingModalTemplate
    price: EscalateManaModeSpec

    @property
    def cost_schema(self):
        return {ESCALATE_MANA_FIELD: self.price.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        return f"fixed-escalate-mana-{template}", effects, schema, mechanics


def fixed_escalate_mana_template(material_rows, *, compile_effect):
    if len(material_rows) != 5:
        return None
    match = _PRICE.fullmatch(material_rows[0][1])
    if match is None:
        return None
    vector, complex_symbols = mana_cost_to_vector(match["mana"].upper())
    if complex_symbols:
        return None
    body = fixed_nonrepeating_modal_template(material_rows[1:], compile_effect=compile_effect)
    if body is None or body.context_prefix:
        return None
    try:
        price = EscalateManaModeSpec(tuple(body.target_schema["modes"]), vector)
        price.validate_schema(body.target_schema)
    except ValueError:
        return None
    return FixedEscalateManaTemplate(body, price)
