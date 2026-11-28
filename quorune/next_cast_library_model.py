"""Value contracts for the same-turn mana-triggered library scan."""

from __future__ import annotations

import math
from typing import Mapping


NEXT_CAST_LIBRARY_ACCESS = "next_cast_library_access"
NEXT_CAST_LIBRARY_MANA_CAPABILITY = "mana.activated.next_cast_library_access"
NEXT_CAST_LIBRARY_MANA_RESULT_VERSION = 8
NEXT_CAST_LIBRARY_SCAN_VERSION = 2
NEXT_CAST_LIBRARY_PROGRESS_VERSION = 9

_MANA_TEXT = "Add {W}{U}{B}{R}{G}."
_FOLLOWUP_TEXT = (
    "When you next cast a spell this turn, exile cards from the top of your library until you exile "
    "an instant or sorcery card with lesser mana value. Until end of turn, you may cast that card "
    "without paying its mana cost. Put each other card exiled this way on the bottom of your library "
    "in a random order."
)


def printed_next_cast_library_mana(cost: str, effect: str) -> bool:
    return (
        " ".join(cost.split()).casefold() == "{4}, {t}"
        and " ".join(effect.split()).casefold() == f"{_MANA_TEXT} {_FOLLOWUP_TEXT}".casefold()
    )


def valid_exclusive_mana_value(value: object) -> bool:
    return (type(value) is int and value >= 0) or (
        type(value) is float and value >= 0 and math.isfinite(value)
    )


def validate_next_cast_library_trigger(trigger) -> None:
    condition = trigger.condition
    marker = condition.get(NEXT_CAST_LIBRARY_ACCESS)
    if (
        trigger.event_kind != "spell.cast" or set(condition) != {NEXT_CAST_LIBRARY_ACCESS}
        or not isinstance(marker, Mapping) or set(marker) != {"schema_version"}
        or type(marker["schema_version"]) is not int or marker["schema_version"] != 1
        or type(trigger.controller) is not str or not trigger.controller
        or type(trigger.active) is not bool or trigger.once is not True
        or type(trigger.created_turn_sequence) is not int or trigger.created_turn_sequence < 0
        or type(trigger.expires_turn_sequence) is not int
        or trigger.expires_turn_sequence != trigger.created_turn_sequence
        or trigger.stack_template != {} or trigger.referred_object_ids
        or type(trigger.source_object_id) is not str or not trigger.source_object_id
        or type(trigger.source_logical_object_id) is not str or not trigger.source_logical_object_id
    ):
        raise ValueError("Next-cast library access requires the exact one-shot turn and source identity")
