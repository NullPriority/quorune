from __future__ import annotations

"""Typed selected activation costs and their serialized forms."""

from dataclasses import dataclass
from typing import Any, Mapping

from .replacement.immutable import FrozenMap, thaw_value


@dataclass(frozen=True, slots=True)
class CostChoice:
    kind: str
    count: int = 1
    zone: str = "battlefield"
    card_type: str | None = None
    another: bool = False
    predicate: FrozenMap | None = None
    counter_amount: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("cost choice kind must be a nonempty string")
        if type(self.count) is not int or self.count < 1:
            raise ValueError("cost choice count must be a positive integer")
        if not isinstance(self.zone, str) or not self.zone.strip():
            raise ValueError("cost choice zone must be a nonempty string")
        if self.card_type is not None and (
            not isinstance(self.card_type, str) or not self.card_type.strip()
        ):
            raise ValueError("cost choice card_type must be null or nonempty")
        if type(self.another) is not bool:
            raise ValueError("cost choice another flag must be boolean")
        from .rules.selected_counter_costs import SELECTED_COUNTER_COST_CAPABILITIES, validate_selected_counter_choice
        if self.kind in SELECTED_COUNTER_COST_CAPABILITIES:
            validate_selected_counter_choice(self)
        elif self.counter_amount is not None:
            raise ValueError("Counter amount requires a typed counter cost")
        if self.predicate is not None:
            if not isinstance(self.predicate, Mapping):
                raise ValueError("cost choice predicate must be an object")
            object.__setattr__(self, "predicate", FrozenMap(self.predicate))
            tap_cost = self.fixed_tap_cost()
            if tap_cost is None:
                if self.count > 10 or self.card_type is not None:
                    raise ValueError(
                        "typed zone-change cost choices require one exact query and fixed count"
                    )
                cost = self.fixed_zone_change_cost()
                if cost is None or self.zone != cost.origin_zone:
                    raise ValueError(
                        "typed zone-change cost choice origin is inconsistent"
                    )
                if self.another and cost.operation != "sacrifice_one":
                    raise ValueError(
                        "another is supported only for typed sacrifice costs"
                    )

    def fixed_zone_change_cost(self) -> Any | None:
        """Return the shared closed zone-change cost represented by this choice."""

        if self.predicate is None:
            return None
        from .additional_cost_vocabulary import (
            FIXED_ZONE_CHANGE_COST_CONTRACTS,
            RETURN_ONE_TO_OWNER_HAND_COST,
            ZONE_CHANGE_COST_KIND,
        )
        from .rules.casting_additional_costs import (
            FixedZoneChangeAdditionalCost,
        )

        from .combat_entry_activations import (
            FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND,
        )

        operation = (
            RETURN_ONE_TO_OWNER_HAND_COST
            if self.kind == FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND
            else self.kind
        )
        contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(operation)
        if contract is None:
            return None
        descriptor = {
                "schema_version": (
                    2
                    if self.kind
                    == FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND
                    else 1
                ),
                "kind": ZONE_CHANGE_COST_KIND,
                "operation": operation,
                "count": self.count,
                "choice_field": contract[2],
                "predicate": thaw_value(self.predicate),
            }
        if self.kind == FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND:
            descriptor["unblocked_attacker"] = True
        return FixedZoneChangeAdditionalCost.from_descriptor(descriptor)

    def fixed_tap_cost(self) -> Any | None:
        """Return the shared closed selected-permanent tap cost, if present."""

        if self.predicate is None:
            return None
        from .rules.activation_costs import (
            FIXED_TAP_ACTIVATION_COST_KIND,
            FixedTapActivationCost,
        )

        if self.kind != FIXED_TAP_ACTIVATION_COST_KIND:
            return None
        return FixedTapActivationCost.from_descriptor(
            {
                "schema_version": 2 if self.predicate.get('colors_any') else 1,
                "kind": self.kind,
                "count": self.count,
                "zone": self.zone,
                "another": self.another,
                "predicate": thaw_value(self.predicate),
            }
        )

    def compact(self) -> dict[str, Any]:
        result: dict[str, Any] = {"k": self.kind, "n": self.count, "z": self.zone}
        if self.card_type:
            result["t"] = self.card_type
        if self.another:
            result["other"] = 1
        if self.predicate is not None:
            result["q"] = thaw_value(self.predicate)
        if self.counter_amount is not None:
            result["amount"] = self.counter_amount
        return result

    def to_dict(self) -> dict[str, Any]:
        result = {
            "kind": self.kind,
            "count": self.count,
            "zone": self.zone,
            "card_type": self.card_type,
            "another": self.another,
        }
        if self.predicate is not None:
            result["predicate"] = thaw_value(self.predicate)
        if self.counter_amount is not None:
            result["counter_amount"] = self.counter_amount
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CostChoice":
        expected = {"kind", "count", "zone", "card_type", "another"}
        if not isinstance(value, Mapping) or set(value) not in {
            frozenset(expected),
            frozenset((*expected, "predicate")),
            frozenset((*expected, "counter_amount")),
        }:
            raise ValueError("cost choices use a closed schema")
        if "counter_amount" in value and value["counter_amount"] is None:
            raise ValueError("Typed counter cost amounts must be nonnull")
        if "predicate" in value and value["predicate"] is None:
            raise ValueError(
                "typed cost choice predicates must be nonnull objects"
            )
        return cls(
            kind=value["kind"],
            count=value["count"],
            zone=value["zone"],
            card_type=value["card_type"],
            another=value["another"],
            counter_amount=value.get("counter_amount"),
            predicate=(
                FrozenMap(value["predicate"])
                if "predicate" in value
                else None
            ),
        )
