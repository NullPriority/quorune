from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .object_predicate import (
    ObjectQueryError,
    ObjectQuerySpec,
    permanent_state_predicate_matches,
    validate_chosen_damage_source_predicate,
)
from .replacement.immutable import FrozenMap


@dataclass(frozen=True, slots=True)
class ObjectQueryResult:
    object_id: str
    ref: str
    printed_name: str
    owner: str
    controller: str
    zone: str
    types: tuple[str, ...] = ()
    subtypes: tuple[str, ...] = ()
    supertypes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    counters: FrozenMap = field(default_factory=FrozenMap)
    mana_value: int = 0
    effective_power: int | None = None
    effective_toughness: int | None = None
    token: bool = False
    tapped: bool = False
    phased_out: bool = False
    known_to_actor: bool = True
    attached_to_ref: str | None = None
    logical_object_id: str = ""
    monstrous_value: int | None = None
    renowned: bool = False
    entered_this_turn: bool = False
    attacking: bool = False
    blocking: bool = False
    enchanted: bool = False
    equipped: bool = False
    modified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.counters, FrozenMap):
            object.__setattr__(self, "counters", FrozenMap(self.counters))
        if type(self.renowned) is not bool:
            raise ValueError("Object query renowned state must be a boolean")
        for field_name in (
            "entered_this_turn",
            "attacking",
            "blocking",
            "enchanted",
            "equipped",
            "modified",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise ValueError(
                    "Object query "
                    f"{field_name.replace('_', '-')} state must be a boolean"
                )


def exact_numeric_characteristic(
    card: Any,
    effective: Mapping[str, Any],
    stat: str,
) -> int | None:
    """Return one represented effective power/toughness without guessing."""

    if stat not in {"power", "toughness"}:
        raise ValueError("Exact numeric characteristics are power or toughness")

    def exact_integer(value: Any) -> int | None:
        if type(value) is int:
            return value
        if type(value) is not str or not value.strip():
            return None
        try:
            return int(value.strip())
        except ValueError:
            return None

    base = exact_integer(
        card.annotations.get(
            f"continuous_{stat}", effective.get(stat)
        )
    )
    if base is None:
        return None
    plus = card.counters.get("+1/+1", 0)
    minus = card.counters.get("-1/-1", 0)
    if type(plus) is not int or type(minus) is not int:
        return None
    duration = card.annotations.get("until_end_of_turn") or {}
    if not isinstance(duration, Mapping):
        return None
    delta = exact_integer(duration.get(stat, 0))
    if delta is None:
        return None
    return base + plus - minus + delta


def object_query_result(
    card: Any,
    effective: Mapping[str, Any],
    *,
    type_parts: tuple[Iterable[str], Iterable[str], Iterable[str]],
    known_to_actor: bool,
    attached_to_ref: str | None,
    entered_this_turn: bool = False,
    attacking: bool | None = None,
    blocking: bool | None = None,
    enchanted: bool = False,
    equipped: bool = False,
    modified: bool | None = None,
) -> ObjectQueryResult:
    types, subtypes, supertypes = type_parts
    return ObjectQueryResult(
        object_id=str(card.object_id),
        logical_object_id=str(card.logical_object_id),
        ref=str(card.ref),
        printed_name=str(card.printed_name),
        owner=str(card.owner),
        controller=str(card.controller),
        zone=str(card.zone),
        types=tuple(sorted(str(value).casefold() for value in types)),
        subtypes=tuple(sorted(str(value).casefold() for value in subtypes)),
        supertypes=tuple(
            sorted(str(value).casefold() for value in supertypes)
        ),
        colors=tuple(
            str(value).upper() for value in effective.get("colors", ())
        ),
        keywords=tuple(
            str(value).casefold() for value in effective.get("keywords", ())
        ),
        counters=FrozenMap(card.counters),
        mana_value=int(effective.get("mana_value") or 0),
        effective_power=exact_numeric_characteristic(
            card, effective, "power"
        ),
        effective_toughness=exact_numeric_characteristic(
            card, effective, "toughness"
        ),
        token=bool(card.is_token),
        tapped=bool(card.tapped),
        phased_out=bool(card.phased_out),
        known_to_actor=known_to_actor,
        attached_to_ref=attached_to_ref,
        monstrous_value=card.monstrous_value,
        renowned=card.renowned,
        entered_this_turn=entered_this_turn,
        attacking=(card.attacking is not None if attacking is None else attacking),
        blocking=(card.blocking is not None if blocking is None else blocking),
        enchanted=enchanted,
        equipped=equipped,
        modified=(
            any(
                type(amount) is int and amount > 0
                for amount in card.counters.values()
            )
            or equipped
            if modified is None
            else modified
        ),
    )


def object_matches_query(
    row: ObjectQueryResult,
    spec: ObjectQuerySpec,
) -> bool:
    types = frozenset(row.types)
    subtypes = frozenset(row.subtypes)
    colors = frozenset(row.colors)
    return bool(
        (not spec.zones or row.zone in spec.zones)
        and (spec.owner is None or row.owner == spec.owner)
        and (spec.controller is None or row.controller == spec.controller)
        and row.controller not in spec.excluded_controllers
        and set(spec.types_all).issubset(types)
        and (not spec.types_any or not types.isdisjoint(spec.types_any))
        and types.isdisjoint(spec.excluded_types)
        and set(spec.subtypes_all).issubset(subtypes)
        and (not spec.subtypes_any or not subtypes.isdisjoint(spec.subtypes_any))
        and subtypes.isdisjoint(spec.excluded_subtypes)
        and set(spec.supertypes_all).issubset(row.supertypes)
        and set(spec.colors_all).issubset(colors)
        and (not spec.colors_any or not set(spec.colors_any).isdisjoint(colors))
        and (spec.colorless is None or (not colors) is spec.colorless)
        and (
            spec.minimum_color_count is None
            or len(colors) >= spec.minimum_color_count
        )
        and set(spec.keywords_all).issubset(row.keywords)
        and (spec.token is None or row.token is spec.token)
        and (spec.tapped is None or row.tapped is spec.tapped)
        and (spec.include_phased_out or not row.phased_out)
        and (
            spec.known_to_actor is None
            or row.known_to_actor is spec.known_to_actor
        )
        and (spec.exclude_ref is None or row.ref != spec.exclude_ref)
        and (
            spec.state_predicate is None
            or permanent_state_predicate_matches(
                spec.state_predicate,
                counters=row.counters,
                entered_this_turn=row.entered_this_turn,
                tapped=row.tapped,
                attacking=row.attacking,
                blocking=row.blocking,
                enchanted=row.enchanted,
                equipped=row.equipped,
                modified=row.modified,
                monstrous=row.monstrous_value is not None,
            )
        )
    )


def query_objects(
    rows: Iterable[ObjectQueryResult],
    spec: ObjectQuerySpec,
) -> tuple[ObjectQueryResult, ...]:
    """Filter immutable rules facts without applying target legality."""

    return tuple(row for row in rows if object_matches_query(row, spec))


__all__ = [
    "ObjectQueryError",
    "ObjectQueryResult",
    "ObjectQuerySpec",
    "exact_numeric_characteristic",
    "object_matches_query",
    "object_query_result",
    "query_objects",
    "validate_chosen_damage_source_predicate",
]
