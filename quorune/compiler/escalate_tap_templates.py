"""Compile one complete modal spell with a fixed creature tap per extra mode."""

from dataclasses import dataclass

from ..escalate_tap_model import ESCALATE_TAP_FIELD, EscalateTapModeSpec
from .modal_templates import FixedNonrepeatingModalTemplate, fixed_nonrepeating_modal_template


@dataclass(frozen=True, slots=True)
class FixedEscalateTapTemplate:
    body: FixedNonrepeatingModalTemplate
    price: EscalateTapModeSpec

    @property
    def cost_schema(self):
        return {ESCALATE_TAP_FIELD: self.price.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        return f"fixed-escalate-tap-{template}", effects, schema, mechanics


def fixed_escalate_tap_template(material_rows, *, compile_effect):
    if len(material_rows) != 5:
        return None
    price = " ".join(material_rows[0][1].replace("—", " ").casefold().split())
    if price != "escalate tap an untapped creature you control.":
        return None
    body = fixed_nonrepeating_modal_template(material_rows[1:], compile_effect=compile_effect)
    if body is None or body.context_prefix:
        return None
    policy = EscalateTapModeSpec(tuple(body.target_schema["modes"]))
    try:
        policy.validate_schema(body.target_schema)
    except ValueError:
        return None
    return FixedEscalateTapTemplate(body, policy)
