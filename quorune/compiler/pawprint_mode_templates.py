"""Compile the printed one-, two-, and three-pawprint mode block."""

from dataclasses import dataclass

from ..pawprint_modes import PAWPRINT_FIELD, PAWPRINT_MECHANIC, PawprintModeBudget
from .modal_templates import FixedNonrepeatingModalTemplate, fixed_nonrepeating_modal_template


@dataclass(frozen=True, slots=True)
class FixedPawprintTemplate:
    body: FixedNonrepeatingModalTemplate
    budget: PawprintModeBudget

    @property
    def cost_schema(self):
        return {PAWPRINT_FIELD: self.budget.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        return f"fixed-pawprint-{template}", effects, schema, (*mechanics, PAWPRINT_MECHANIC)


def fixed_pawprint_mode_template(material_rows, *, compile_effect):
    if len(material_rows) != 4:
        return None
    first = material_rows[0]
    if first[1].casefold() != "choose up to five {p} worth of modes. you may choose the same mode more than once.":
        return None
    rows = [(first[0], "Choose one or more —", first[2])]
    for count, (raw, material, span) in enumerate(material_rows[1:], 1):
        prefix, separator, body = material.partition(" — ")
        if not separator or prefix != "{P}" * count or not body:
            return None
        rows.append((raw, "• " + body, span))
    body = fixed_nonrepeating_modal_template(rows, compile_effect=compile_effect)
    if body is None or body.context_prefix:
        return None
    return FixedPawprintTemplate(body, PawprintModeBudget())
