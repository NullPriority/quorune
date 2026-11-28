"""Typed search completion context and the affected-player search entry route."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..model import StackItem
from ..replacement_effects import ReplacementChoiceRequired
from ..rules.affected_library_search_batch import (
    SEARCH_BATCH_VERSION,
    advance_exile_land_search,
    commit_exile_land_search,
    verify_exile_land_search,
)
from ..rules.library_search_rules import library_search_constraint


@dataclass(frozen=True, slots=True)
class SemanticSearchCompletionContext:
    seat: str
    response: Mapping[str, Any]
    continuation: dict[str, Any]
    stack_ref: str
    item: StackItem
    frame: dict[str, Any]
    effect: dict[str, Any]
    options: frozenset[str]


def begin_exile_land_search(host, item, effect, *, remaining, destination, note, instruction_pointer):
    if effect.get("schema_version") != SEARCH_BATCH_VERSION:
        return False
    batch = verify_exile_land_search(host, item, effect)
    if effect.get("commit") is True:
        try:
            commit_exile_land_search(host, item, effect, batch)
        except ReplacementChoiceRequired as required:
            from ..replacement_decisions import issue_replacement_order_choice

            issue_replacement_order_choice(
                host, item=item, effect=effect, remaining=remaining, destination=destination,
                note=note, instruction_pointer=instruction_pointer, required=required,
            )
            return True
        host._continue_resolution(
            stack_ref=item.ref, effects=remaining, destination=destination, note=note,
            instruction_pointer=instruction_pointer + 1,
        )
        return True
    searcher = effect["searching_player"]
    if not library_search_constraint(host, searcher).prohibited:
        return False
    context = SemanticSearchCompletionContext(
        seat=searcher, item=item, effect=effect, stack_ref=item.ref, response={},
        continuation={"remaining": remaining, "destination": destination, "note": note},
        frame={"instruction_pointer": instruction_pointer}, options=frozenset(),
    )
    advance_exile_land_search(host, context, (), searched=False)
    return True
