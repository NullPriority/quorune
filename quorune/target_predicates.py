from __future__ import annotations

"""Closed target-predicate evaluation outside the engine facade."""

from typing import Any, Mapping, Protocol, Set

from .model import CardInstance, StackItem
from .relative_power_target import (
    RelativePowerTargetCondition,
    RelativePowerTargetError,
    current_effective_creature_power,
)
from .targets import TargetGroup


class TargetPredicateError(ValueError):
    """A target predicate or its typed condition is unsupported or malformed."""


_EXILE_ZONE = "exile"
_VOID_COUNTER = "void"


class TargetPredicateHost(Protocol):
    state: Any

    def _numeric_stat(self, object_id: str, stat: str) -> int: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _relative_power_matches(
    host: TargetPredicateHost,
    group: TargetGroup,
    card: CardInstance | None,
) -> bool:
    if card is None:
        return False
    try:
        condition = RelativePowerTargetCondition.from_dict(
            group.resolution_condition
        )
        source = host.state.cards.get(condition.source.object_id)
        if (
            source is not None
            and source.zone == "battlefield"
            and source.logical_object_id == condition.source.logical_object_id
            and source.phased_out
        ):
            raise TargetPredicateError(
                "Relative-power source phasing requires a typed LKI transition"
            )
        source_is_current = bool(
            source is not None
            and source.zone == "battlefield"
            and not source.phased_out
            and source.logical_object_id == condition.source.logical_object_id
        )
        current_source_power = (
            current_effective_creature_power(host, source)
            if source_is_current
            else None
        )
        return condition.permits(
            target_power=host._numeric_stat(card.object_id, "power"),
            current_source_power=current_source_power,
            use_last_known=not source_is_current,
        )
    except RelativePowerTargetError as exc:
        raise TargetPredicateError(str(exc)) from exc


def target_predicate_matches(
    host: TargetPredicateHost,
    group: TargetGroup,
    row: Mapping[str, Any],
    *,
    types: Set[str],
    supertypes: Set[str],
    colors: Set[str],
    derived: Mapping[str, bool],
    source_ref: str | None = None,
) -> bool:
    """Evaluate the closed predicate vocabulary for one normalized target row."""

    predicate = group.predicate
    if not predicate:
        return True
    card = row.get("card")
    if predicate == "artifact_or_enchantment_or_nonbasic_land":
        return bool(
            derived["artifact"]
            or derived["enchantment"]
            or (derived["land"] and "basic" not in supertypes)
        )
    if predicate == "permanent_card":
        return bool(
            types.intersection(
                {
                    "artifact",
                    "battle",
                    "creature",
                    "enchantment",
                    "land",
                    "planeswalker",
                }
            )
        )
    if predicate == "damageable":
        return bool(
            row["category"] == "player"
            or types.intersection({"battle", "creature", "planeswalker"})
        )
    if predicate == "player_or_planeswalker":
        return row["category"] == "player" or "planeswalker" in types
    if predicate == "void_counter":
        return bool(
            isinstance(card, CardInstance)
            and row.get("zone") == _EXILE_ZONE
            and int(card.counters.get(_VOID_COUNTER, 0)) > 0
        )
    if predicate == "artifact_source":
        return bool(
            row.get("zone") == "stack"
            and "artifact" in set(row.get("stack_source_types") or ())
        )
    if predicate == "triggered_ability":
        stack_item = row.get("stack_item")
        return bool(
            isinstance(stack_item, StackItem)
            and stack_item.kind == "triggered_ability"
        )
    if predicate == "activated_ability":
        stack_item = row.get("stack_item")
        return bool(
            isinstance(stack_item, StackItem)
            and stack_item.kind == "activated_ability"
        )
    if predicate == "nonblue_spell":
        return row.get("category") == "spell" and "U" not in colors
    if predicate == "not_source_attachment":
        source = next(
            (
                candidate
                for candidate in host.state.cards.values()
                if candidate.ref == source_ref
                and candidate.zone == "battlefield"
                and not candidate.phased_out
            ),
            None,
        )
        return bool(
            source is not None
            and isinstance(card, CardInstance)
            and source.attached_to != card.object_id
        )
    if predicate == "power_less_than_source":
        return _relative_power_matches(
            host,
            group,
            card if isinstance(card, CardInstance) else None,
        )
    raise TargetPredicateError(f"Unsupported target predicate {predicate!r}")


__all__ = [
    "TargetPredicateError",
    "TargetPredicateHost",
    "target_predicate_matches",
]
