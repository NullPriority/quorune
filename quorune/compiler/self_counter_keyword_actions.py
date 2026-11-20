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


__all__ = [
    "FixedSelfCounterKeywordActionTemplate",
    "SelfCounterKeywordAction",
    "fixed_self_counter_keyword_action_template",
]
