"""Closed compiler production for a mandatory discard and sacrifice."""

from __future__ import annotations

from dataclasses import dataclass

from ..rules.fixed_payment_groups import (
    FIXED_DISCARD_SACRIFICE_FIELD, WHOLE_DISCARD_SACRIFICE_VERSION,
    fixed_discard_sacrifice_costs, whole_discard_sacrifice_leaves,
)
from . import regex_working_set as re
from .spell_additional_cost_templates import FixedZoneChangeAdditionalCostTemplate, fixed_zone_change_additional_cost_template


_PREFIX = "As an additional cost to cast this spell, "


@dataclass(frozen=True, slots=True)
class FixedDiscardSacrificeTemplate:
    leaves: tuple[FixedZoneChangeAdditionalCostTemplate, FixedZoneChangeAdditionalCostTemplate]
    template_id: str = "spell-additional-cost-fixed-discard-sacrifice-v1"

    @property
    def cost_schema(self):
        return {FIXED_DISCARD_SACRIFICE_FIELD: 1, "additional_costs": [dict(leaf.descriptor) for leaf in self.leaves]}


@dataclass(frozen=True, slots=True)
class WholeDiscardSacrificeTemplate:
    sacrifice_first: bool
    template_id: str = "spell-additional-cost-whole-discard-sacrifice-v1"

    @property
    def cost_schema(self):
        leaves = whole_discard_sacrifice_leaves(sacrifice_first=self.sacrifice_first)
        return {FIXED_DISCARD_SACRIFICE_FIELD: WHOLE_DISCARD_SACRIFICE_VERSION,
                "additional_costs": [leaf.to_descriptor() for leaf in leaves]}


def fixed_discard_sacrifice_template(text: str) -> FixedDiscardSacrificeTemplate | WholeDiscardSacrificeTemplate | None:
    match = re.fullmatch(r"As an additional cost to cast this spell, (?P<body>[^.!?]+)\.", text.strip(), re.I)
    if match is None:
        return None
    parts = re.split(r"\s+and\s+", match["body"], flags=re.I)
    if len(parts) != 2:
        return None
    normalized = tuple(part.casefold() for part in parts)
    automatic = ("sacrifice all permanents you control", "discard your hand")
    if normalized in (automatic, automatic[::-1]):
        return WholeDiscardSacrificeTemplate(normalized == automatic)
    first, second = (fixed_zone_change_additional_cost_template(_PREFIX + part + ".") for part in parts)
    if first is None or second is None or not fixed_discard_sacrifice_costs((first.descriptor, second.descriptor)):
        return None
    return FixedDiscardSacrificeTemplate((first, second))
