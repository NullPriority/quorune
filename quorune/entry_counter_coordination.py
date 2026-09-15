from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .errors import StateInvariantError
from .entry_counter_model import DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT
from .semantic_runtime.self_entry_counters import (
    dynamic_self_entry_counter_amounts,
)
from .semantic_runtime.zone_replacements import (
    PreparedZoneChange,
    prepare_zone_change_replacement,
)
from .replacement_effects import (
    ReplacementChoiceRequired,
    ReplacementContinuation,
    replacement_choice_payload,
)


_PILOT_ROLE = "pilot"


class EntryCounterCoordinationHost(Protocol):
    permissions: Any
    state: Any

    def _semantic_frame(
        self,
        item: Any,
        *,
        instruction_pointer: int,
        pending_choice_id: str | None = None,
    ) -> dict[str, Any]: ...

    def _validate_semantic_frame(
        self, frame: Mapping[str, Any], item: Any
    ) -> None: ...

    def _continue_resolution(
        self,
        *,
        stack_ref: str,
        effects: list[dict[str, Any]],
        destination: str | None,
        note: str,
        instruction_pointer: int = 0,
        entry_replacement_selections: Sequence[
            str | Mapping[str, Any]
        ] = (),
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class ResolvingEntryPreparation:
    destination: str | None
    replacement: PreparedZoneChange | None
    suspended: bool = False


def prepare_resolving_entry_replacement(
    host: EntryCounterCoordinationHost,
    *,
    item: Any,
    destination: str | None,
    note: str,
    instruction_pointer: int,
    selections: Sequence[str | Mapping[str, Any]],
    error_type: type[Exception],
) -> ResolvingEntryPreparation:
    """Prepare a resolving permanent's final move or suspend once."""

    entry_card: Any | None = None
    entry_destination: str | None = None
    entry_characteristics: Mapping[str, Any] | None = None
    if item.context.get("copy_permanent_spell"):
        if not item.card_object_id:
            raise StateInvariantError(
                "A permanent spell copy requires a copy object"
            )
        entry_card = host.state.cards[item.card_object_id]
        entry_destination = "battlefield"
        entry_characteristics = copy.deepcopy(
            dict(item.context.get("copy_permanent_characteristics", {}))
        )
    elif item.card_object_id:
        candidate = host.state.cards[item.card_object_id]
        if candidate.zone == "stack":
            entry_card = candidate
            entry_destination = (
                destination or item.default_destination or "graveyard"
            )
    if entry_card is None or entry_destination is None:
        return ResolvingEntryPreparation(None, None)
    raw_amounts = item.context.get(DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT)
    if raw_amounts is None:
        frozen_amounts = dict(
            dynamic_self_entry_counter_amounts(
                host,
                card=entry_card,
                destination=entry_destination,
                destination_controller=item.controller,
                mana_colors_spent=item.mana_colors_spent,
            )
        )
        if frozen_amounts:
            item.context[DYNAMIC_SELF_ENTRY_AMOUNTS_CONTEXT] = dict(
                frozen_amounts
            )
    elif (
        not isinstance(raw_amounts, Mapping)
        or any(
            type(component_id) is not str
            or not component_id
            or type(amount) is not int
            or amount < 0
            for component_id, amount in raw_amounts.items()
        )
    ):
        raise error_type("Frozen dynamic entry counter amounts are malformed")
    else:
        frozen_amounts = dict(raw_amounts)
    try:
        prepared = prepare_zone_change_replacement(
            host,
            entry_card,
            entry_destination,
            destination_controller=item.controller,
            entry_characteristics=entry_characteristics,
            self_entry_counter_amounts=(
                frozen_amounts if frozen_amounts else None
            ),
            mana_colors_spent=item.mana_colors_spent,
            selections=tuple(selections),
            error_type=error_type,
        )
    except ReplacementChoiceRequired as required:
        issue_resolving_entry_replacement_choice(
            host,
            item=item,
            destination=destination,
            note=note,
            instruction_pointer=instruction_pointer,
            selections=selections,
            required=required,
        )
        return ResolvingEntryPreparation(
            entry_destination,
            None,
            suspended=True,
        )
    return ResolvingEntryPreparation(entry_destination, prepared)


def issue_resolving_entry_replacement_choice(
    host: EntryCounterCoordinationHost,
    *,
    item: Any,
    destination: str | None,
    note: str,
    instruction_pointer: int,
    selections: Sequence[str | Mapping[str, Any]],
    required: ReplacementChoiceRequired,
) -> None:
    """Suspend a permanent's final as-enters transaction before mutation."""

    pending = required.pending
    chooser = pending.choice.chooser
    decision = host.permissions.issue(
        kind="replacement.order",
        role=_PILOT_ROLE,
        actors=[chooser],
        allowed_actions=["choose"],
        payload_by_actor={
            chooser: replacement_choice_payload(
                pending, required.effects
            )
        },
        continuation={
            "replacement_resume_kind": "resolving_entry",
            "stack_ref": item.ref,
            "destination": destination,
            "note": note,
            "instruction_pointer": instruction_pointer,
            "semantic_frame": host._semantic_frame(
                item,
                instruction_pointer=instruction_pointer,
            ),
            "replacement_selections": [
                dict(value) if isinstance(value, Mapping) else value
                for value in selections
            ],
            "replacement_batch": required.batch.to_dict(),
            "replacement_effects": [
                effect.to_dict() for effect in required.effects
            ],
        },
    )
    decision.continuation["semantic_frame"]["pending_choice_id"] = (
        decision.decision_id
    )


def resume_resolving_entry_replacement(
    host: EntryCounterCoordinationHost,
    restored: ReplacementContinuation,
    selection: str | Mapping[str, Any],
    *,
    error_type: type[Exception],
) -> None:
    item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == restored.stack_ref
        ),
        None,
    )
    if item is None:
        raise error_type(
            "Entry replacement continuation stack object no longer exists"
        )
    host._validate_semantic_frame(restored.thaw_semantic_frame(), item)
    host._continue_resolution(
        stack_ref=item.ref,
        effects=[],
        destination=restored.destination,
        note=restored.note,
        instruction_pointer=restored.instruction_pointer,
        entry_replacement_selections=(
            *restored.replacement_selections,
            selection,
        ),
    )


__all__ = [
    "issue_resolving_entry_replacement_choice",
    "prepare_resolving_entry_replacement",
    "ResolvingEntryPreparation",
    "resume_resolving_entry_replacement",
]
