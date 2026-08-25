from __future__ import annotations

"""Canonical legality and payment for activation object costs."""

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from ..object_predicate import ObjectQuerySpec
from ..object_query import object_query_result, query_objects
from ..tap_state import set_permanent_tapped
from .casting_additional_costs import fixed_zone_change_cost_candidates


FIXED_TAP_ACTIVATION_COST_KIND = "tap"
_FIXED_TAP_FIELDS = frozenset(
    {"schema_version", "kind", "count", "zone", "another", "predicate"}
)
_PERMANENT_CARD_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)


class FixedTapActivationCostError(ValueError):
    """A fixed selected-permanent tap cost is malformed or unpayable."""


class ActivationCostHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class FixedTapActivationCost:
    """Tap a fixed number of untapped controlled permanents matching one query."""

    count: int
    predicate: ObjectQuerySpec
    another: bool = False
    schema_version: int = 1
    kind: str = FIXED_TAP_ACTIVATION_COST_KIND
    zone: str = "battlefield"

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost schema version is unsupported"
            )
        if self.kind != FIXED_TAP_ACTIVATION_COST_KIND:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost kind is unsupported"
            )
        if type(self.count) is not int or self.count < 1:
            raise FixedTapActivationCostError(
                "Fixed tap activation costs require a positive count"
            )
        if self.zone != "battlefield" or type(self.another) is not bool:
            raise FixedTapActivationCostError(
                "Fixed tap activation costs require a closed battlefield choice"
            )
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise FixedTapActivationCostError(
                "Fixed tap activation costs require a typed object query"
            )
        type_axis = self.predicate.types_all
        subtype_axis = self.predicate.subtypes_all
        if (len(type_axis) == 1) is (len(subtype_axis) == 1):
            raise FixedTapActivationCostError(
                "Fixed tap activation costs require one type or one subtype"
            )
        if type_axis and type_axis[0] not in _PERMANENT_CARD_TYPES:
            raise FixedTapActivationCostError(
                "Fixed tap activation costs require a permanent card type"
            )
        expected = ObjectQuerySpec(
            zones=("battlefield",),
            controller="$actor",
            types_all=type_axis,
            subtypes_all=subtype_axis,
            tapped=False,
            known_to_actor=True,
            exclude_ref="$source" if self.another else None,
        )
        if self.predicate != expected:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost query is outside the closed family"
            )

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedTapActivationCost":
        if not isinstance(value, Mapping) or set(value) != _FIXED_TAP_FIELDS:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost descriptor fields are closed"
            )
        try:
            predicate = ObjectQuerySpec.from_dict(value["predicate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost query is malformed"
            ) from exc
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            count=value["count"],
            zone=value["zone"],
            another=value["another"],
            predicate=predicate,
        )

    def bound_predicate(self, actor: str, source_ref: str) -> ObjectQuerySpec:
        if type(actor) is not str or not actor or type(source_ref) is not str or not source_ref:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost identities are required"
            )
        return replace(
            self.predicate,
            controller=actor,
            exclude_ref=source_ref if self.another else None,
        )


@dataclass(frozen=True, slots=True)
class FixedTapCostCandidate:
    object_id: str
    object_ref: str
    logical_object_id: str

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value
            for value in (self.object_id, self.object_ref, self.logical_object_id)
        ):
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost candidate identity is required"
            )


@dataclass(frozen=True, slots=True)
class FixedTapCostPlan:
    seat: str
    source_object_id: str
    source_logical_object_id: str
    cost: FixedTapActivationCost
    selected: tuple[FixedTapCostCandidate, ...]

    def __post_init__(self) -> None:
        if not all(
            type(value) is str and value
            for value in (
                self.seat,
                self.source_object_id,
                self.source_logical_object_id,
            )
        ):
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost plan identity is required"
            )
        if not isinstance(self.cost, FixedTapActivationCost):
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost plan requires a typed cost"
            )
        if not isinstance(self.selected, tuple) or any(
            not isinstance(candidate, FixedTapCostCandidate)
            for candidate in self.selected
        ):
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost plan candidates must be typed"
            )
        if len(self.selected) != self.cost.count:
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost plan has the wrong selection count"
            )
        identities = tuple(candidate.object_id for candidate in self.selected)
        if len(set(identities)) != len(identities):
            raise FixedTapActivationCostError(
                "Fixed tap activation-cost plan candidates must be distinct"
            )


def _fixed_tap_candidates(
    host: ActivationCostHost,
    *,
    actor: str,
    source: Any,
    cost: FixedTapActivationCost,
) -> tuple[FixedTapCostCandidate, ...]:
    rows = []
    for object_id in host.state.players[actor].zones["battlefield"]:
        card = host.state.cards[object_id]
        effective = host._effective_card_data(card)
        rows.append(
            object_query_result(
                card,
                effective,
                type_parts=host._type_parts(
                    str(effective.get("type_line") or "")
                ),
                known_to_actor=True,
                attached_to_ref=None,
            )
        )
    return tuple(
        FixedTapCostCandidate(
            object_id=row.object_id,
            object_ref=row.ref,
            logical_object_id=row.logical_object_id,
        )
        for row in query_objects(
            rows, cost.bound_predicate(actor, source.ref)
        )
    )


def fixed_tap_cost_candidates(
    host: ActivationCostHost,
    *,
    actor: str,
    source: Any,
    cost: FixedTapActivationCost,
) -> tuple[str, ...]:
    """Return the public offer set through the cost's immutable query."""

    return tuple(
        candidate.object_ref
        for candidate in _fixed_tap_candidates(
            host, actor=actor, source=source, cost=cost
        )
    )


def _submitted_tap_refs(
    response: Mapping[str, Any], *, count: int
) -> tuple[str, ...]:
    has_cards = "cost_cards" in response
    has_objects = "cost_objects" in response
    if has_cards and has_objects:
        raise FixedTapActivationCostError(
            "Submit only one activation cost-object field"
        )
    raw = (
        response.get("cost_cards")
        if has_cards
        else response.get("cost_objects") if has_objects else ()
    )
    if (
        not isinstance(raw, (list, tuple))
        or len(raw) != count
        or any(type(value) is not str or not value for value in raw)
    ):
        raise FixedTapActivationCostError(
            f"Activation requires exactly {count} tap-cost object reference(s)"
        )
    values = tuple(raw)
    if len(set(values)) != len(values):
        raise FixedTapActivationCostError(
            "The same object cannot pay a tap cost twice"
        )
    return values


def prepare_fixed_tap_cost(
    host: ActivationCostHost,
    *,
    seat: str,
    source: Any,
    choice: Any,
    response: Mapping[str, Any],
) -> FixedTapCostPlan:
    """Validate a complete fixed tap selection before authoritative mutation."""

    cost = choice.fixed_tap_cost()
    if cost is None:
        raise FixedTapActivationCostError(
            "Activation does not carry a fixed tap cost"
        )
    if (
        source.zone != "battlefield"
        or source.controller != seat
        or source.phased_out
    ):
        raise FixedTapActivationCostError(
            "Activation source is no longer available for its tap cost"
        )
    submitted = _submitted_tap_refs(response, count=cost.count)
    available = {
        candidate.object_ref: candidate
        for candidate in _fixed_tap_candidates(
            host, actor=seat, source=source, cost=cost
        )
    }
    selected = tuple(available.get(ref) for ref in submitted)
    if any(candidate is None for candidate in selected):
        raise FixedTapActivationCostError(
            "Tap-cost objects must be untapped matching permanents you control"
        )
    return FixedTapCostPlan(
        seat=seat,
        source_object_id=source.object_id,
        source_logical_object_id=source.logical_object_id,
        cost=cost,
        selected=tuple(candidate for candidate in selected if candidate is not None),
    )


def commit_fixed_tap_cost(
    host: ActivationCostHost, plan: FixedTapCostPlan
) -> list[str]:
    """Revalidate and atomically commit one prepared fixed tap selection."""

    source = host.state.cards.get(plan.source_object_id)
    if (
        source is None
        or source.zone != "battlefield"
        or source.controller != plan.seat
        or source.phased_out
        or source.logical_object_id != plan.source_logical_object_id
    ):
        raise FixedTapActivationCostError(
            "Activation source changed before tap-cost commitment"
        )
    current = {
        candidate.object_id: candidate
        for candidate in _fixed_tap_candidates(
            host, actor=plan.seat, source=source, cost=plan.cost
        )
    }
    if any(current.get(candidate.object_id) != candidate for candidate in plan.selected):
        raise FixedTapActivationCostError(
            "A selected tap-cost object changed before commitment"
        )
    for candidate in plan.selected:
        set_permanent_tapped(
            host,
            candidate.object_ref,
            actor=plan.seat,
            tapped=True,
            reason="activated ability cost",
            logical_object_id=candidate.logical_object_id,
            log=False,
        )
    host._log(
        plan.seat,
        "cost.tap_selected",
        f"{plan.seat} tapped {len(plan.selected)} object(s) to activate {source.ref}.",
        {
            "source": source.ref,
            "objects": [candidate.object_ref for candidate in plan.selected],
        },
        importance=1,
        changed_objects=[candidate.object_id for candidate in plan.selected],
        changed_players=[plan.seat],
    )
    return [candidate.object_id for candidate in plan.selected]


def pay_fixed_tap_cost(
    host: ActivationCostHost,
    *,
    seat: str,
    source: Any,
    choice: Any,
    response: Mapping[str, Any],
) -> list[str]:
    return commit_fixed_tap_cost(
        host,
        prepare_fixed_tap_cost(
            host,
            seat=seat,
            source=source,
            choice=choice,
            response=response,
        ),
    )


def activation_choice_candidates(
    host: ActivationCostHost,
    actor: str,
    source: Any,
    choice: Any,
) -> tuple[str, ...]:
    """Return the canonical legal objects for one activation cost choice."""

    typed_cost = choice.fixed_zone_change_cost()
    if typed_cost is not None:
        return fixed_zone_change_cost_candidates(
            host,
            actor=actor,
            cost=typed_cost,
            exclude_object_id=(source.object_id if choice.another else None),
        )
    tap_cost = choice.fixed_tap_cost()
    if tap_cost is not None:
        return fixed_tap_cost_candidates(
            host,
            actor=actor,
            source=source,
            cost=tap_cost,
        )
    candidates: list[str] = []
    for object_id in host.state.players[actor].zones.get(choice.zone, []):
        card = host.state.cards[object_id]
        if choice.zone == "battlefield":
            if card.controller != actor or card.phased_out:
                continue
        elif card.owner != actor:
            continue
        if choice.another and card.object_id == source.object_id:
            continue
        if choice.card_type:
            type_line = str(
                host._effective_card_data(card).get("type_line") or ""
            ).casefold()
            if choice.card_type not in type_line:
                continue
        candidates.append(card.ref)
    return tuple(candidates)


__all__ = [
    "FIXED_TAP_ACTIVATION_COST_KIND",
    "FixedTapActivationCost",
    "FixedTapActivationCostError",
    "FixedTapCostCandidate",
    "FixedTapCostPlan",
    "activation_choice_candidates",
    "commit_fixed_tap_cost",
    "fixed_tap_cost_candidates",
    "pay_fixed_tap_cost",
    "prepare_fixed_tap_cost",
]
