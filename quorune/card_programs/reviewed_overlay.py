from __future__ import annotations

"""Canonical precedence between typed programs and reviewed compatibility views."""

from typing import Any, Iterable

from ..carddb import CardRecord
from ..rules.event_subscriptions import FixedEventSubscriptionSet
from ..semantics import SemanticProgram


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


__all__ = ["shadowed_reviewed_multi_event_keys"]
