from __future__ import annotations

"""Closed lowering for fixed self-counter keyword actions."""

from dataclasses import dataclass
from enum import Enum
from . import regex_working_set as re
from typing import Any, Mapping

from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_ACTION = re.compile(
    rf"(?P<action>adapt|monstrosity)\s+"
    rf"(?P<amount>{FIXED_COUNT_PATTERN})\.?",
    re.IGNORECASE,
)


class SelfCounterKeywordAction(str, Enum):
    ADAPT = "adapt"
    MONSTROSITY = "monstrosity"


@dataclass(frozen=True, slots=True)
class FixedSelfCounterKeywordActionTemplate:
    action: SelfCounterKeywordAction
    amount: int

    def __post_init__(self) -> None:
        if not isinstance(self.action, SelfCounterKeywordAction):
            raise ValueError("Self-counter keyword action is unsupported")
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Self-counter keyword actions require a positive fixed amount"
            )

    @property
    def template_id(self) -> str:
        return f"keyword-action-{self.action.value}-fixed-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "fixed_self_counter_keyword_action",
                "action": self.action.value,
                "amount": self.amount,
                "source": "$source",
            },
        )

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (self.action.value, "cr-122-counters")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return self.template_id, self.effects, None, self.mechanics


def fixed_self_counter_keyword_action_template(
    text: str,
) -> FixedSelfCounterKeywordActionTemplate | None:
    """Recognize one complete positive fixed Adapt or Monstrosity action."""

    match = _ACTION.fullmatch(text.strip())
    if match is None:
        return None
    amount = fixed_number(match.group("amount"))
    if amount <= 0:
        return None
    return FixedSelfCounterKeywordActionTemplate(
        action=SelfCounterKeywordAction(match.group("action").casefold()),
        amount=amount,
    )


def controlled_counter_monstrosity_activation(ability, text, *, public_timing):
    """Lower the exact live counter-total definition on a nonmana activation."""
    if (not re.fullmatch(r"Monstrosity X, where X is the number of counters among creatures you control\.?",
                         text.strip(), re.I)
            or ability.mana_ability or ability.zones != ("battlefield",)):
        return None
    from ..rules.controlled_counter_monstrosity import (
        CONTROLLED_COUNTER_MONSTROSITY_MECHANIC,
        controlled_counter_monstrosity_cost_is_closed, controlled_counter_monstrosity_effect,
    )
    from .activated_costs import activated_ability_cost

    if not controlled_counter_monstrosity_cost_is_closed(activated_ability_cost(ability, public_timing=public_timing)):
        return None
    return ("keyword-action-monstrosity-controlled-creature-counters-v1",
            (controlled_counter_monstrosity_effect(),), None,
            ("monstrosity", "cr-122-counters", CONTROLLED_COUNTER_MONSTROSITY_MECHANIC))


__all__ = [
    "FixedSelfCounterKeywordActionTemplate",
    "SelfCounterKeywordAction",
    "fixed_self_counter_keyword_action_template",
]
