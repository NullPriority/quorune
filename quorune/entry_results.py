from __future__ import annotations

from typing import Any, Protocol, Sequence

from .counter_placement import commit_counter_events_from_resolution
from .entry_designations import EntryDesignationKind, validate_designation
from .semantic_runtime.intents import SetCardDesignationIntent
from .entry_keyword_grants import (
    commit_entry_keyword_grants,
    EntryKeywordGrant,
    EntryKeywordGrantError,
)


class PreparedEntryResult(Protocol):
    event: Any
    effects: Sequence[Any]
    journal: Sequence[Any]
    keyword_grants: Sequence[EntryKeywordGrant]


def commit_entry_designations(
    host: Any,
    card: Any,
    *,
    color: str | None,
    creature_type: str | None,
) -> None:
    """Retain entry choices through the canonical card-designation intent owner."""
    choices = (
        (EntryDesignationKind.COLOR, color),
        (EntryDesignationKind.CREATURE_TYPE, creature_type),
    )
    for kind, value in choices:
        if value is not None:
            host.set_card_designation_intent(
                SetCardDesignationIntent(
                    object_ref=card.ref,
                    designation=kind.annotation,
                    value=validate_designation(kind, value),
                    actor=card.controller,
                    reason="intrinsic battlefield-entry choice",
                )
            )


def commit_prepared_entry_results(
    host: Any,
    prepared: PreparedEntryResult,
    card: Any,
    *,
    reason: str,
    log: bool,
    error_type: type[Exception],
) -> None:
    """Commit typed nested-counter and layer-6 results after one entry."""

    commit_counter_events_from_resolution(
        host,
        prepared,
        reason=reason,
        log=log,
        error_type=error_type,
        dispatch_events=False,
    )
    try:
        commit_entry_keyword_grants(host, card, prepared.keyword_grants)
    except EntryKeywordGrantError as exc:
        raise error_type(str(exc)) from exc


__all__ = ["PreparedEntryResult", "commit_prepared_entry_results"]
