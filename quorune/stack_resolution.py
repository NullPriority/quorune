from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol

from .aura import aura_resolution_move_kwargs
from .errors import StateInvariantError
from .evoke import EVOKE_PAYMENT_FIELD, validate_evoke_payment_marker
from .model import CardInstance, StackItem
from .semantic_runtime.zone_replacements import PreparedZoneChange
from .stack_counter import oracle_has_intrinsic_counter_prohibition


_COPY_TERM = "copy"


class GenericStackResolutionQuery(Protocol):
    semantics: Any

    def card_record(self, object_id: str) -> Any: ...

    def _trusted_generic_spell(self, record: Any) -> bool: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


class StackResolutionCompletionHost(Protocol):
    state: Any

    def move_card(
        self,
        object_id: str,
        destination: str,
        **kwargs: Any,
    ) -> CardInstance: ...


@dataclass(frozen=True, slots=True)
class EmptyStackResolution:
    destination: str | None
    note: str
    provenance: str


def trusted_generic_empty_resolution(
    host: GenericStackResolutionQuery,
    item: StackItem,
    program: Any,
) -> EmptyStackResolution | None:
    """Plan an empty resolution only for an exact spell without an effect program."""

    if program is not None:
        return None
    provenance: str | None = None
    if item.kind == "spell" and item.card_object_id:
        record = host.card_record(item.card_object_id)
        if record and host._trusted_generic_spell(record):
            provenance = "trusted_generic_permanent_spell"
        elif record and oracle_has_intrinsic_counter_prohibition(
            host.semantics,
            str(record.oracle_id),
            current_trusted=host.semantic_program_is_current_trusted,
        ):
            provenance = "trusted_intrinsic_counter_prohibition_spell"
    elif item.kind == "spell_copy":
        if item.context.get("copy_permanent_spell"):
            provenance = "trusted_generic_permanent_spell_copy"
    if provenance is None:
        return None
    return EmptyStackResolution(
        destination=item.default_destination,
        note="Trusted exact spell resolved with no executable resolution effects",
        provenance=provenance,
    )


def complete_stack_resolution(
    host: StackResolutionCompletionHost,
    *,
    item: StackItem,
    destination: str | None,
    prepared_replacement: PreparedZoneChange | None,
) -> None:
    """Commit the final physical stack-object transition once choices end."""

    item.context.pop("currently_resolving", None)
    host.state.stack.remove(item)
    if item.context.get("copy_permanent_spell"):
        if not item.card_object_id:
            raise StateInvariantError(
                "A permanent spell copy requires a copy object"
            )
        card = host.state.cards[item.card_object_id]
        if not card.is_spell_copy or card.zone != "stack":
            raise StateInvariantError(
                "Permanent spell-copy object left the stack early"
            )
        characteristics = copy.deepcopy(
            dict(item.context.get("copy_permanent_characteristics", {}))
        )
        card.printed_name = str(
            item.context.get("copy_permanent_name")
            or item.label.removesuffix(" " + _COPY_TERM)
        )
        card.annotations["copy_overrides"] = characteristics
        # CR 608.3f/707.10f: this same spell-copy object becomes a token
        # permanent. It is not a newly created token.
        card.object_kind = "token"
        card.is_token = True
        host.move_card(
            card.object_id,
            "battlefield",
            controller=item.controller,
            **aura_resolution_move_kwargs(item),
            prepared_replacement=prepared_replacement,
            reason="permanent spell copy resolved",
            log=False,
            semantic_events=True,
        )
        return
    if not item.card_object_id:
        return
    card = host.state.cards[item.card_object_id]
    if card.zone != "stack":
        return
    evoked = validate_evoke_payment_marker(
        item.context.get(EVOKE_PAYMENT_FIELD)
    )
    if evoked:
        card.annotations["evoked"] = True
    host.move_card(
        card.object_id,
        destination or "graveyard",
        controller=item.controller,
        **aura_resolution_move_kwargs(item),
        prepared_replacement=prepared_replacement,
        reason="spell resolved",
        log=False,
        semantic_events=True,
    )
    if evoked and card.zone != "battlefield":
        card.annotations.pop("evoked", None)


__all__ = [
    "complete_stack_resolution",
    "EmptyStackResolution",
    "GenericStackResolutionQuery",
    "StackResolutionCompletionHost",
    "trusted_generic_empty_resolution",
]
