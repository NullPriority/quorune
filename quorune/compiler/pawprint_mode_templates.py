"""Compile the printed one-, two-, and three-pawprint mode block."""

from dataclasses import dataclass

from ..pawprint_modes import (
    PAWPRINT_FIELD, PAWPRINT_MECHANIC, PawprintModeBudget,
    REPEATABLE_MODE_FIELD, REPEATABLE_MODE_MECHANIC, FixedRepeatableModeChoices,
)
from .modal_templates import FixedNonrepeatingModalTemplate, fixed_nonrepeating_modal_template


@dataclass(frozen=True, slots=True)
class FixedRepeatableModalTemplate:
    body: FixedNonrepeatingModalTemplate
    budget: PawprintModeBudget | FixedRepeatableModeChoices

    @property
    def cost_schema(self):
        field = REPEATABLE_MODE_FIELD if isinstance(self.budget, FixedRepeatableModeChoices) else PAWPRINT_FIELD
        return {field: self.budget.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        if isinstance(self.budget, FixedRepeatableModeChoices):
            return f"fixed-repeatable-mode-{template}", effects, schema, (*mechanics, REPEATABLE_MODE_MECHANIC)
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
    return FixedRepeatableModalTemplate(body, PawprintModeBudget())


def fixed_repeatable_mode_template(material_rows, *, compile_effect):
    if len(material_rows) != 4:
        return None
    raw, header, span = material_rows[0]
    if header.casefold() != "choose three. you may choose the same mode more than once.":
        return None
    rows = ((raw, "Choose one or more —", span), *material_rows[1:])
    body = fixed_nonrepeating_modal_template(rows, compile_effect=compile_effect)
    return FixedRepeatableModalTemplate(body, FixedRepeatableModeChoices()) if body is not None and not body.context_prefix else None
