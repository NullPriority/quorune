from __future__ import annotations

"""Immutable target-set snapshots for fixed permanent counter effects."""

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping, Protocol, Sequence

from .counter_placement import (
    CounterPlacementError,
    CounterPlacementHost,
    CounterPlacementRequest,
    CounterPlacementResult,
    place_counters,
)
from .object_query import ObjectQueryResult
from .util import stable_json


CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD = "controlled_creature_counter_choice"
CONTROLLED_CREATURE_COUNTER_SNAPSHOTS = "_controlled_creature_counter_incarnations"
OPPONENT_CREATURE_COUNTER_CHOICE_VERSION = 2
OPPONENT_CREATURE_COUNTER_CHOICE_CAPABILITY = "counter.choice.target_opponent_creature"


def controlled_creature_counter_choice_effect() -> dict[str, Any]:
    return {
        "op": "choose_objects",
        "selector": {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_all": ["creature"],
            "controller_relation": "you",
        },
        "minimum": 1,
        "maximum": 1,
        "prompt": "Choose a creature you control for a +1/+1 counter.",
        "then": [{"op": "add_counter_selected", "counter": "+1/+1", "amount": 1}],
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD: 1,
    }


def opponent_creature_counter_choice_effect(player: str = "$target.0") -> dict[str, Any]:
    return {
        **controlled_creature_counter_choice_effect(),
        "player": player,
        "prompt": "Choose a creature you control for a deathtouch counter.",
        "then": [{"op": "add_counter_selected", "counter": "deathtouch", "amount": 1}],
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD: OPPONENT_CREATURE_COUNTER_CHOICE_VERSION,
    }


def controlled_creature_counter_choice_dependencies(
    value: Mapping[str, Any], *, resolved_player: str | None = None,
) -> tuple[str, ...]:
    if CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD not in value:
        return ()
    from .replacement.immutable import thaw_value

    from .rules.permanent_resolution_choices import COUNTER_KEYWORDS_VERSION, controlled_counter_keywords_dependencies

    data = thaw_value(value)
    version = data.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD)
    if version == COUNTER_KEYWORDS_VERSION:
        return controlled_counter_keywords_dependencies(data)
    if version == OPPONENT_CREATURE_COUNTER_CHOICE_VERSION:
        expected = opponent_creature_counter_choice_effect(
            resolved_player if resolved_player is not None else "$target.0",
        )
        if (data != expected or type(data[CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD]) is not int
                or type(data["minimum"]) is not int or type(data["maximum"]) is not int
                or type(data["then"][0]["amount"]) is not int):
            raise ValueError("The opponent counter choice requires one exact deathtouch instruction")
        return (
            OPPONENT_CREATURE_COUNTER_CHOICE_CAPABILITY, "counter.producer.fixed_effect",
            "counter.characteristic.keyword", "combat.damage.assignment.deathtouch",
            "damage.result.deathtouch", "target.revalidate_resolution",
        )
    if (data != controlled_creature_counter_choice_effect()
            or any(type(data[key]) is not int for key in (
                "minimum", "maximum", CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD,
            ))
            or type(data["then"][0]["amount"]) is not int):
        raise ValueError("A controlled creature counter choice requires its exact untargeted instruction")
    return ("counter.producer.fixed_effect",)


class CounterPlacementTargetSetError(ValueError):
    """A fixed target-set counter instruction or snapshot is invalid."""


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise CounterPlacementTargetSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class CounterPlacementTargetPermanent:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str

    def __post_init__(self) -> None:
        for field, value in (
            ("object ID", self.object_id),
            ("logical object ID", self.logical_object_id),
            ("reference", self.ref),
            ("controller", self.controller),
        ):
            _nonempty(value, field=f"Counter-target permanent {field}")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "ref": self.ref,
            "controller": self.controller,
        }


@dataclass(frozen=True, slots=True)
class CounterPlacementTargetSnapshot:
    maximum_targets: int
    permanents: tuple[CounterPlacementTargetPermanent, ...]

    def __post_init__(self) -> None:
        if type(self.maximum_targets) is not int or self.maximum_targets <= 0:
            raise CounterPlacementTargetSetError(
                "Counter-target maximum must be a positive exact integer"
            )
        permanents = tuple(self.permanents)
        if any(
            not isinstance(value, CounterPlacementTargetPermanent)
            for value in permanents
        ):
            raise CounterPlacementTargetSetError(
                "Counter-target snapshots require typed permanents"
            )
        if len(permanents) > self.maximum_targets:
            raise CounterPlacementTargetSetError(
                "Counter-target snapshot exceeds its maximum"
            )
        for values, label in (
            ((value.object_id for value in permanents), "object"),
            ((value.logical_object_id for value in permanents), "logical object"),
            ((value.ref for value in permanents), "reference"),
        ):
            normalized = tuple(values)
            if len(normalized) != len(set(normalized)):
                raise CounterPlacementTargetSetError(
                    f"Counter-target snapshots require unique {label} identities"
                )
        object.__setattr__(self, "permanents", permanents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "maximum_targets": self.maximum_targets,
            "permanents": [value.to_dict() for value in self.permanents],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


class CounterPlacementTargetQuery(Protocol):
    def counter_target_active_seats(self) -> tuple[str, ...]: ...

    def counter_target_apnap_order(self) -> tuple[str, ...]: ...

    def counter_target_object_rows(
        self,
        actor: str,
        refs: tuple[str, ...],
    ) -> tuple[ObjectQueryResult, ...]: ...


class CounterPlacementTargetHost(
    CounterPlacementHost,
    CounterPlacementTargetQuery,
    Protocol,
):
    pass


def snapshot_counter_placement_targets(
    query: CounterPlacementTargetQuery,
    *,
    actor: str,
    refs: Sequence[str],
    maximum_targets: int,
) -> CounterPlacementTargetSnapshot:
    """Freeze and canonically order the still-legal selected permanents."""

    _nonempty(actor, field="Counter-target actor")
    if type(maximum_targets) is not int or maximum_targets <= 0:
        raise CounterPlacementTargetSetError(
            "Counter-target maximum must be a positive exact integer"
        )
    if not isinstance(refs, (list, tuple)):
        raise CounterPlacementTargetSetError(
            "Counter targets must be an ordered sequence"
        )
    normalized = tuple(refs)
    if (
        len(normalized) > maximum_targets
        or any(type(ref) is not str or not ref for ref in normalized)
        or len(normalized) != len(set(normalized))
    ):
        raise CounterPlacementTargetSetError(
            "Counter targets must be unique nonempty references within the maximum"
        )
    active = tuple(query.counter_target_active_seats())
    order = tuple(query.counter_target_apnap_order())
    if (
        actor not in active
        or len(active) != len(set(active))
        or len(order) != len(active)
        or set(order) != set(active)
    ):
        raise CounterPlacementTargetSetError(
            "Counter targets require a complete APNAP view"
        )
    rows = tuple(query.counter_target_object_rows(actor, normalized))
    if len(rows) != len(normalized) or {row.ref for row in rows} != set(normalized):
        raise CounterPlacementTargetSetError(
            "Counter target query did not resolve the submitted set exactly"
        )
    order_index = {seat: index for index, seat in enumerate(order)}
    if any(
        row.zone != "battlefield"
        or row.phased_out
        or row.controller not in order_index
        or not row.object_id
        or not row.logical_object_id
        or not row.ref
        for row in rows
    ):
        raise CounterPlacementTargetSetError(
            "Counter target query returned an invalid battlefield identity"
        )
    snapshot = CounterPlacementTargetSnapshot(
        maximum_targets=maximum_targets,
        permanents=tuple(
            CounterPlacementTargetPermanent(
                object_id=row.object_id,
                logical_object_id=row.logical_object_id,
                ref=row.ref,
                controller=row.controller,
            )
            for row in sorted(
                rows,
                key=lambda row: (
                    order_index[row.controller],
                    row.logical_object_id,
                    row.object_id,
                    row.ref,
                ),
            )
        ),
    )
    return snapshot


def resolve_counter_placement_targets(
    host: CounterPlacementTargetHost,
    *,
    actor: str,
    refs: Sequence[str],
    maximum_targets: int,
    counter_name: str,
    amount: int,
    reason: str,
    source_ref: str | None = None,
    replacement_selections: Sequence[str | dict[str, Any]] = (),
) -> tuple[CounterPlacementResult, ...]:
    """Resolve one fixed target set through the canonical counter owner."""

    _nonempty(actor, field="Counter-target actor")
    normalized_counter = " ".join(
        _nonempty(counter_name, field="Counter-target counter").casefold().split()
    )
    _nonempty(normalized_counter, field="Counter-target counter")
    _nonempty(reason, field="Counter-target reason")
    from .counter_names import EXISTING_COUNTER_AMOUNT
    if amount != EXISTING_COUNTER_AMOUNT and (type(amount) is not int or amount <= 0):
        raise CounterPlacementTargetSetError(
            "Counter-target amount must be a positive exact integer"
        )
    if source_ref is not None:
        _nonempty(source_ref, field="Counter-target source")
    snapshot = snapshot_counter_placement_targets(
        host,
        actor=actor,
        refs=refs,
        maximum_targets=maximum_targets,
    )
    if amount == EXISTING_COUNTER_AMOUNT:
        from .counter_doubling import resolve_effect_counters_on_refs
        try:return resolve_effect_counters_on_refs(host,actor=actor,object_refs=tuple(p.ref for p in snapshot.permanents),
            counter_name=normalized_counter,amount=amount,reason=reason,source_ref=source_ref,selections=replacement_selections)
        except CounterPlacementError as exc:raise CounterPlacementTargetSetError(str(exc)) from exc
    try:
        return place_counters(
            host,
            tuple(
                CounterPlacementRequest(
                    subject_kind="permanent",
                    subject_id=value.object_id,
                    counter_name=normalized_counter,
                    amount=amount,
                    placing_player=actor,
                    source_ref=source_ref,
                )
                for value in snapshot.permanents
            ),
            selections=replacement_selections,
            reason=reason,
        )
    except CounterPlacementError as exc:
        raise CounterPlacementTargetSetError(str(exc)) from exc


__all__ = [
    "CounterPlacementTargetHost",
    "CounterPlacementTargetPermanent",
    "CounterPlacementTargetQuery",
    "CounterPlacementTargetSetError",
    "CounterPlacementTargetSnapshot",
    "resolve_counter_placement_targets",
    "snapshot_counter_placement_targets",
]
