from __future__ import annotations

"""Typed selected activation costs and their serialized forms."""

from dataclasses import dataclass
from typing import Any, Mapping

from .rules.announced_subtype_sacrifice import ANNOUNCED_SUBTYPE_SACRIFICE_KIND, validate_announced_subtype_choice
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
    tap_schema_version: int | None = None
    return_schema_version: int | None = None
    sacrifice_schema_version: int | None = None
    predicate_union: FrozenMap | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("cost choice kind must be a nonempty string")
        if self.kind == ANNOUNCED_SUBTYPE_SACRIFICE_KIND:
            if type(self.count) is not int:
                raise ValueError("The announced subtype choice count must be an integer")
            validate_announced_subtype_choice(self)
        elif type(self.count) is not int or self.count < 1:
            raise ValueError("cost choice count must be a positive integer")
        if not isinstance(self.zone, str) or not self.zone.strip():
            raise ValueError("cost choice zone must be a nonempty string")
        if self.card_type is not None and (
            not isinstance(self.card_type, str) or not self.card_type.strip()
        ):
            raise ValueError("cost choice card_type must be null or nonempty")
        if type(self.another) is not bool:
            raise ValueError("cost choice another flag must be boolean")
        if self.tap_schema_version is not None and (
            type(self.tap_schema_version) is not int or self.tap_schema_version != 3
            or self.kind != "tap" or self.predicate is None or self.card_type is not None
        ):
            raise ValueError("New tap-cost versions require an explicit typed tap choice")
        if self.return_schema_version is not None:
            from .qualified_return_cost_model import QUALIFIED_RETURN_COST_VERSION
            if (type(self.return_schema_version) is not int or self.return_schema_version != QUALIFIED_RETURN_COST_VERSION
                    or self.kind != 'return_one_to_owner_hand' or self.predicate is None
                    or self.card_type is not None or self.another):
                raise ValueError('New return-cost versions require an explicit subtype return choice')
        if self.sacrifice_schema_version is not None:
            from .keyword_sacrifice_cost_model import KEYWORD_SACRIFICE_COST_VERSION
            if (type(self.sacrifice_schema_version) is not int or self.sacrifice_schema_version!=KEYWORD_SACRIFICE_COST_VERSION
                    or self.kind!='sacrifice_one' or self.predicate is None or self.card_type is not None
                    or self.return_schema_version is not None or self.tap_schema_version is not None):
                raise ValueError('Keyword sacrifice choices require their explicit version and typed predicate')
        if self.predicate_union is not None:
            from .sacrifice_union_model import COLORED_OR_ARTIFACT_CREATURE_UNION_VERSION, SacrificePredicateUnion

            union = SacrificePredicateUnion.from_dict(self.predicate_union)
            if (union.schema_version != COLORED_OR_ARTIFACT_CREATURE_UNION_VERSION
                    or self.kind != "sacrifice_one" or self.count != 1 or self.predicate is None
                    or any(value is not None for value in (self.tap_schema_version, self.return_schema_version,
                                                           self.sacrifice_schema_version))):
                raise ValueError("An activation sacrifice union requires its isolated typed creature cost")
            object.__setattr__(self, "predicate_union", FrozenMap(union.to_dict()))
        if self.kind == "forage" and (
            self.count != 1 or self.zone != "battlefield" or self.card_type is not None or self.another
            or any(value is not None for value in (self.predicate, self.tap_schema_version,
                self.return_schema_version, self.sacrifice_schema_version, self.predicate_union))
        ):
            raise ValueError("Forage is one closed selected-cost choice")
        if self.kind == "sacrifice_up_to_three" and (
            self.count != 3 or self.zone != "battlefield" or self.card_type is not None or self.another
            or any(value is not None for value in (self.predicate, self.tap_schema_version,
                self.return_schema_version, self.sacrifice_schema_version, self.predicate_union))
        ):
            raise ValueError("Bounded sacrifice requires the closed up-to-three battlefield choice")
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
            SACRIFICE_ONE_COST,
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
        if self.kind == ANNOUNCED_SUBTYPE_SACRIFICE_KIND:
            operation = SACRIFICE_ONE_COST
        contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(operation)
        if contract is None:
            return None
        version = self.sacrifice_schema_version or self.return_schema_version or 1
        if self.kind == FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND:
            version = 2
        elif self.predicate_union is not None:
            version = 5
        descriptor = {
                "schema_version": version,
                "kind": ZONE_CHANGE_COST_KIND,
                "operation": operation,
                "count": 1 if self.kind == ANNOUNCED_SUBTYPE_SACRIFICE_KIND else self.count,
                "choice_field": contract[2],
                "predicate": thaw_value(self.predicate),
            }
        if self.kind == FIXED_UNBLOCKED_ATTACKER_RETURN_COST_KIND:
            descriptor["unblocked_attacker"] = True
        if self.predicate_union is not None:
            descriptor["predicate_union"] = thaw_value(self.predicate_union)
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
                "schema_version": self.tap_schema_version or (2 if self.predicate.get('colors_any') else 1),
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
        if self.tap_schema_version is not None:
            result["tap_schema_version"] = self.tap_schema_version
        if self.return_schema_version is not None:
            result['return_schema_version'] = self.return_schema_version
        if self.sacrifice_schema_version is not None:
            result['sacrifice_schema_version'] = self.sacrifice_schema_version
        if self.predicate_union is not None:
            result["predicate_union"] = thaw_value(self.predicate_union)
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
        if self.tap_schema_version is not None:
            result["tap_schema_version"] = self.tap_schema_version
        if self.return_schema_version is not None:
            result['return_schema_version'] = self.return_schema_version
        if self.sacrifice_schema_version is not None:
            result['sacrifice_schema_version'] = self.sacrifice_schema_version
        if self.predicate_union is not None:
            result["predicate_union"] = thaw_value(self.predicate_union)
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CostChoice":
        expected = {"kind", "count", "zone", "card_type", "another"}
        if not isinstance(value, Mapping) or set(value) not in {
            frozenset(expected),
            frozenset((*expected, "predicate")),
            frozenset((*expected, "counter_amount")),
            frozenset((*expected, "predicate", "tap_schema_version")),
            frozenset((*expected, 'predicate', 'return_schema_version')),
            frozenset((*expected, 'predicate', 'sacrifice_schema_version')),
            frozenset((*expected, "predicate", "predicate_union")),
        }:
            raise ValueError("cost choices use a closed schema")
        if "counter_amount" in value and value["counter_amount"] is None:
            raise ValueError("Typed counter cost amounts must be nonnull")
        if "tap_schema_version" in value and value["tap_schema_version"] is None:
            raise ValueError("Explicit tap-cost versions must be nonnull")
        if 'return_schema_version' in value and value['return_schema_version'] is None:
            raise ValueError('Explicit return-cost versions must be nonnull')
        if 'sacrifice_schema_version' in value and value['sacrifice_schema_version'] is None:
            raise ValueError('Explicit sacrifice-cost versions must be nonnull')
        if "predicate_union" in value and value["predicate_union"] is None:
            raise ValueError("A sacrifice predicate union must be nonnull")
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
            tap_schema_version=value.get("tap_schema_version"),
            return_schema_version=value.get('return_schema_version'),
            sacrifice_schema_version=value.get('sacrifice_schema_version'),
            predicate_union=value.get("predicate_union"),
            predicate=(
                FrozenMap(value["predicate"])
                if "predicate" in value
                else None
            ),
        )
