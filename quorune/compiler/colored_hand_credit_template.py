"""An optional fixed generic credit for a selected colored hand collection."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..rules.colored_hand_credit import (
    COLORED_HAND_CREDIT,
    ColoredHandCredit,
    colored_hand_credit_dependencies,
)
from . import regex_working_set as re


_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_TEMPLATE_ID = "optional-colored-hand-exile-credit-v1"


@dataclass(frozen=True, slots=True)
class ColoredHandCreditTemplate:
    spec: ColoredHandCredit
    template_id: str = _TEMPLATE_ID

    @property
    def cost_schema(self):
        return {COLORED_HAND_CREDIT: self.spec.to_dict()}


def colored_hand_credit_template(text: str):
    match = re.fullmatch(
        r"As an additional cost to cast this spell, you may exile any number of "
        r"(?P<color>white|blue|black|red|green) cards from your hand\. "
        r"This spell costs \{2\} less to cast for each card exiled this way\.",
        " ".join(text.split()), re.IGNORECASE,
    )
    return ColoredHandCreditTemplate(ColoredHandCredit(_COLORS[match["color"].casefold()])) if match else None


def closed_colored_hand_credit_spell(program, *, body_is_closed):
    """Check the typed spell price and independently admit its result."""
    if (program.event != "resolve" or program.active_zone != "stack"
            or not program.ability_id.startswith("spell:")):
        return False
    try:
        required = colored_hand_credit_dependencies(program.cost_schema)
    except (TypeError, ValueError):
        return False
    if not required or not set(required).issubset(program.capability_dependencies):
        return False

    prefix = _TEMPLATE_ID + "+"
    template = str(program.provenance.get("template_id") or "")
    if not template.startswith(prefix):
        return False
    body = replace(
        program,
        cost_schema=None,
        provenance={**program.provenance, "template_id": template[len(prefix):]},
        coverage=[value for value in program.coverage if value != "cr-601-casting-spells"],
    )
    return body_is_closed(body)
