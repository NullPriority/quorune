from __future__ import annotations

"""Canonical precedence between typed programs and reviewed compatibility views."""

from typing import Any, Iterable

from ..carddb import CardRecord
from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..semantics import SemanticProgram
from ..library_search_model import (
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FixedCountedLibrarySearchTemplate,
)


_SOURCE_REF_EVENT_CONDITION = {
    "field": "card",
    "op": "eq",
    "value": "$source.ref",
}


def _canonical_effects(program: SemanticProgram) -> list[dict[str, Any]]:
    effects = [dict(effect) for effect in program.effects]
    for effect in effects:
        if effect.get("op") == "draw" and "private" not in effect:
            effect["private"] = True
    return effects


def _same_trigger_body(
    generated: SemanticProgram,
    reviewed: SemanticProgram,
) -> bool:
    return bool(
        generated.active_zone == reviewed.active_zone
        and _canonical_effects(generated) == _canonical_effects(reviewed)
        and generated.handlers == reviewed.handlers
        and generated.target_schema == reviewed.target_schema
        and generated.cost_schema == reviewed.cost_schema
        and generated.destination == reviewed.destination
        and generated.requires_arbiter == reviewed.requires_arbiter
        and reviewed.event_condition is None
    )


def _reviewed_self_event_matches_subscription(
    record: CardRecord,
    reviewed_event: str,
    subscription_event: str,
) -> bool:
    if not reviewed_event.endswith(".self"):
        return False
    normalized = reviewed_event.removesuffix(".self")
    if normalized == subscription_event:
        return True
    printed_types = {
        value.casefold()
        for value in record.type_line.partition("—")[0].split()
    }
    return bool(
        "artifact" in printed_types
        and normalized == "artifact.graveyard"
        and subscription_event == "permanent.graveyard"
    )


def shadowed_reviewed_multi_event_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Identify complete split compatibility views owned by one typed ability."""

    reviewed_values = tuple(reviewed_programs)
    shadowed: set[str] = set()
    for generated in generated_programs:
        subscriptions = FixedEventSubscriptionSet.from_condition(
            generated.event_condition
        )
        if subscriptions is None or generated.trust_level != "trusted":
            continue
        if any(
            subscription.condition is None
            or dict(subscription.condition) != _SOURCE_REF_EVENT_CONDITION
            for subscription in subscriptions.subscriptions
        ):
            continue
        body_matches = tuple(
            reviewed
            for reviewed in reviewed_values
            if reviewed.trust_level == "trusted"
            and _same_trigger_body(generated, reviewed)
        )
        matched: list[SemanticProgram] = []
        for subscription in subscriptions.subscriptions:
            candidates = tuple(
                reviewed
                for reviewed in body_matches
                if _reviewed_self_event_matches_subscription(
                    record,
                    reviewed.event,
                    subscription.event,
                )
            )
            if len(candidates) != 1:
                matched = []
                break
            matched.append(candidates[0])
        if len({program.key for program in matched}) == len(
            subscriptions.subscriptions
        ):
            shadowed.update(program.key for program in matched)
    return shadowed


def shadowed_reviewed_counted_search_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Prefer a current bound search only over the same reviewed instruction.

    Persisted reviewed programs remain compatibility payloads. This precedence
    applies while assembling current programs, with matching source hashes,
    events, targets, costs and effect semantics rather than a card identity.
    """
    reviewed = {program.key: program for program in reviewed_programs}
    shadowed = set()
    for generated in generated_programs:
        old = reviewed.get(generated.key)
        if (
            old is None or generated.trust_level != "trusted"
            or old.trust_level != "trusted"
            or FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID not in generated.capability_dependencies
        ):
            continue
        if not generated.capability_closure or generated.capability_closure.get("trusted") is not True:
            continue
        if any(
            getattr(generated, field) != getattr(old, field)
            for field in (
                "active_zone", "event", "event_condition", "target_schema",
                "cost_schema", "handlers", "destination", "requires_arbiter",
            )
        ):
            continue
        if any(
            not generated.provenance.get(field)
            or generated.provenance[field] != old.provenance.get(field)
            for field in ("source_oracle_hash", "source_rulings_hash")
        ):
            continue
        if len(generated.effects) != len(old.effects):
            continue
        equal = True
        for current, previous in zip(generated.effects, old.effects):
            current = dict(current)
            previous = dict(previous)
            if current.get("op") != "search" or current.get("schema_version") != 2:
                equal = False
                break
            try:
                FixedCountedLibrarySearchTemplate.from_effect(current)
            except (ValueError, TypeError, KeyError):
                equal = False
                break
            current.pop("schema_version")
            if current.get("shuffle_before_placement") is False:
                current.pop("shuffle_before_placement")
            if previous.get("searching_player") == "$controller":
                previous.pop("searching_player")
            if current != previous:
                equal = False
                break
        if equal:
            shadowed.add(old.key)
    return shadowed


def shadowed_reviewed_event_return_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Prefer an incarnation-bound return over its same-source legacy body."""
    from ..event_card_return import EVENT_CARD_RETURN_CAPABILITY, event_card_return_effect
    reviewed_values = tuple(reviewed_programs)
    shadowed: set[str] = set()
    for generated in generated_programs:
        if (
            generated.trust_level != "trusted"
            or EVENT_CARD_RETURN_CAPABILITY not in generated.capability_dependencies
            or not generated.capability_closure
            or generated.capability_closure.get("trusted") is not True
            or tuple(dict(effect) for effect in generated.effects) != (event_card_return_effect(),)
            or generated.event not in {"permanent.graveyard.self", "creature.dies.self"}
            or generated.event_condition is not None
        ):
            continue
        for old in reviewed_values:
            if old.trust_level != "trusted" or len(old.effects) != 1:
                continue
            effect = dict(old.effects[0])
            effect.pop("reason", None)
            if effect != {"op": "move", "card": "$source", "destination": "hand"}:
                continue
            if any(getattr(old, field) != getattr(generated, field) for field in (
                "active_zone", "event_condition", "target_schema", "cost_schema",
                "handlers", "destination", "requires_arbiter",
            )):
                continue
            if not _reviewed_self_event_matches_subscription(record, old.event, generated.event.removesuffix(".self")):
                continue
            if any(not generated.provenance.get(field) or generated.provenance[field] != old.provenance.get(field)
                   for field in ("source_oracle_hash", "source_rulings_hash")):
                continue
            shadowed.add(old.key)
    return shadowed


def shadowed_reviewed_program_keys(
    record: CardRecord,
    generated_programs: Iterable[SemanticProgram],
    reviewed_programs: Iterable[SemanticProgram],
) -> set[str]:
    """Apply shared current-program precedence in both assembly paths."""
    generated = tuple(generated_programs)
    reviewed = tuple(reviewed_programs)
    return shadowed_reviewed_multi_event_keys(
        record, generated, reviewed,
    ) | shadowed_reviewed_counted_search_keys(
        record, generated, reviewed,
    ) | shadowed_reviewed_event_return_keys(record, generated, reviewed)


__all__ = [
    "shadowed_reviewed_multi_event_keys", "shadowed_reviewed_counted_search_keys",
    "shadowed_reviewed_program_keys",
]
