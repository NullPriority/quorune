from __future__ import annotations

"""Typed fixed life, mana, and binary alternative casting costs."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias

from ..additional_cost_vocabulary import (
    ALTERNATIVE_ADDITIONAL_COST_KIND,
    DISCARD_ONE_COST,
    EXILE_ONE_FROM_BATTLEFIELD_COST,
    EXILE_ONE_FROM_GRAVEYARD_COST,
    EXILE_ONE_FROM_HAND_COST,
    FIXED_LIFE_PAYMENT_COST_KIND,
    FIXED_MANA_PAYMENT_COST_KIND,
    RETURN_ONE_TO_OWNER_HAND_COST,
    SACRIFICE_ONE_COST,
)
from .reveal_additional_cost import FixedRevealAdditionalCost,REVEAL_ADDITIONAL_COST_CAPABILITY,fixed_reveal_additional_cost
from .tap_additional_cost import FixedTapAdditionalCost,TAP_ADDITIONAL_COST_CAPABILITY,fixed_tap_additional_cost
from .casting_additional_costs import (
    AdditionalCostError, announced_cost_x, ANNOUNCED_X_EXILE_CAPABILITY, ANNOUNCED_X_LIFE_CAPABILITY,announced_zone_cost_capabilities,
    FixedZoneChangeAdditionalCost,
    fixed_zone_change_additional_cost,
)


_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_FIXED_PAYMENT_FIELDS = frozenset(
    {"schema_version", "kind", "amount"}
)
_FIXED_MANA_FIELDS = frozenset(
    {"schema_version", "kind", "requirements"}
)
_ALTERNATIVE_FIELDS = frozenset(
    {"schema_version", "kind", "options"}
)


@dataclass(frozen=True, slots=True)
class FixedLifePaymentAdditionalCost:
    """Pay one positive fixed life amount as an additional casting cost."""

    amount: int
    schema_version: int = 1
    kind: str = FIXED_LIFE_PAYMENT_COST_KIND

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AdditionalCostError(
                "Fixed life additional-cost schema version is unsupported"
            )
        if self.kind != FIXED_LIFE_PAYMENT_COST_KIND:
            raise AdditionalCostError(
                "Fixed life additional-cost kind is unsupported"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise AdditionalCostError(
                "Fixed life additional costs require a positive amount"
            )

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedLifePaymentAdditionalCost":
        if not isinstance(value, Mapping) or set(value) != _FIXED_PAYMENT_FIELDS:
            raise AdditionalCostError(
                "Fixed life additional-cost descriptor fields are closed"
            )
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            amount=value["amount"],
        )

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "amount": self.amount,
        }


@dataclass(frozen=True, slots=True)
class AnnouncedXLifeAdditionalCost:
    schema_version: int = 1
    kind: str = 'announced_x_life'
    parameter: str = 'x'

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1 or self.kind != 'announced_x_life' or self.parameter != 'x':
            raise AdditionalCostError('Declared life costs require the closed X parameter')

    def amount_for(self, value):
        return announced_cost_x(value)

    def to_descriptor(self):
        return {'schema_version': 1, 'kind': self.kind, 'parameter': self.parameter}

    @classmethod
    def from_descriptor(cls, value):
        if not isinstance(value, Mapping) or set(value) != {'schema_version', 'kind', 'parameter'}:
            raise AdditionalCostError('Declared life additional-cost fields are closed')
        return cls(**dict(value))


def announced_x_life_additional_cost(value):
    return AnnouncedXLifeAdditionalCost.from_descriptor(value) if value.get('kind') == 'announced_x_life' else None


def announced_additional_cost_dependencies(cost_schema):
    """Retain runtime authority for typed declaration-dependent cost prices."""
    if not isinstance(cost_schema, Mapping):
        return ()
    required = set()
    for raw in cost_schema.get('additional_costs', ()):
        if not isinstance(raw, Mapping):
            continue
        if raw.get('kind') == 'announced_x_life':
            AnnouncedXLifeAdditionalCost.from_descriptor(raw)
            required.update((ANNOUNCED_X_LIFE_CAPABILITY, 'casting.additional_cost.fixed_life_payment'))
        if raw.get('kind') == 'zone_change' and raw.get('schema_version') == 3:
            cost=FixedZoneChangeAdditionalCost.from_descriptor(raw)
            required.update((*announced_zone_cost_capabilities(cost),fixed_zone_change_additional_cost_capability(cost)))
    return tuple(sorted(required))


@dataclass(frozen=True, slots=True)
class FixedManaPaymentAdditionalCost:
    """Pay one positive ordinary fixed mana vector as an additional cost."""

    requirements: tuple[tuple[str, int], ...]
    schema_version: int = 1
    kind: str = FIXED_MANA_PAYMENT_COST_KIND

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AdditionalCostError(
                "Fixed mana additional-cost schema version is unsupported"
            )
        if self.kind != FIXED_MANA_PAYMENT_COST_KIND:
            raise AdditionalCostError(
                "Fixed mana additional-cost kind is unsupported"
            )
        if not isinstance(self.requirements, tuple):
            raise AdditionalCostError(
                "Fixed mana additional-cost requirements must be immutable"
            )
        requirements = dict(self.requirements)
        if (
            tuple(key for key, _ in self.requirements) != _MANA_KEYS
            or len(requirements) != len(_MANA_KEYS)
            or any(
                type(amount) is not int or amount < 0
                for amount in requirements.values()
            )
            or not any(requirements.values())
        ):
            raise AdditionalCostError(
                "Fixed mana additional costs require one positive ordinary vector"
            )

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedManaPaymentAdditionalCost":
        if not isinstance(value, Mapping) or set(value) != _FIXED_MANA_FIELDS:
            raise AdditionalCostError(
                "Fixed mana additional-cost descriptor fields are closed"
            )
        raw = value.get("requirements")
        if not isinstance(raw, Mapping) or set(raw) != set(_MANA_KEYS):
            raise AdditionalCostError(
                "Fixed mana additional-cost requirements are malformed"
            )
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            requirements=tuple((key, raw[key]) for key in _MANA_KEYS),
        )

    @property
    def requirements_dict(self) -> dict[str, int]:
        return dict(self.requirements)

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "requirements": self.requirements_dict,
        }


FixedAdditionalCostLeaf: TypeAlias = (
    FixedZoneChangeAdditionalCost
    | FixedLifePaymentAdditionalCost
    | FixedManaPaymentAdditionalCost
    | FixedRevealAdditionalCost
    | FixedTapAdditionalCost
)


def fixed_additional_cost_leaf(
    value: Mapping[str, Any],
) -> FixedAdditionalCostLeaf:
    """Parse one non-nested leaf from the closed alternative-cost algebra."""

    reveal=fixed_reveal_additional_cost(value)
    if reveal is not None:return reveal
    tap=fixed_tap_additional_cost(value)
    if tap is not None:return tap
    try:
        zone_change = fixed_zone_change_additional_cost(value)
        if zone_change is not None:
            if zone_change.count_parameter is not None:
                raise AdditionalCostError("Declared-X alternative additional costs need their own composition")
            return zone_change
    except AdditionalCostError:
        raise
    if value.get("kind") == FIXED_LIFE_PAYMENT_COST_KIND:
        return FixedLifePaymentAdditionalCost.from_descriptor(value)
    if value.get("kind") == FIXED_MANA_PAYMENT_COST_KIND:
        return FixedManaPaymentAdditionalCost.from_descriptor(value)
    raise AdditionalCostError(
        "Alternative additional-cost leaf is outside the closed family"
    )


@dataclass(frozen=True, slots=True)
class FixedAlternativeAdditionalCost:
    """Choose exactly one of two independently typed additional-cost leaves."""

    options: tuple[FixedAdditionalCostLeaf, ...]
    schema_version: int = 1
    kind: str = ALTERNATIVE_ADDITIONAL_COST_KIND

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise AdditionalCostError(
                "Alternative additional-cost schema version is unsupported"
            )
        if self.kind != ALTERNATIVE_ADDITIONAL_COST_KIND:
            raise AdditionalCostError(
                "Alternative additional-cost kind is unsupported"
            )
        if len(self.options) != 2:
            raise AdditionalCostError(
                "Alternative additional costs require exactly two options"
            )
        descriptors = [option.to_descriptor() for option in self.options]
        if descriptors[0] == descriptors[1]:
            raise AdditionalCostError(
                "Alternative additional-cost options must be distinct"
            )

    @classmethod
    def from_descriptor(
        cls, value: Mapping[str, Any]
    ) -> "FixedAlternativeAdditionalCost":
        if not isinstance(value, Mapping) or set(value) != _ALTERNATIVE_FIELDS:
            raise AdditionalCostError(
                "Alternative additional-cost descriptor fields are closed"
            )
        raw_options = value.get("options")
        if not isinstance(raw_options, list) or len(raw_options) != 2:
            raise AdditionalCostError(
                "Alternative additional-cost options are malformed"
            )
        if any(not isinstance(option, Mapping) for option in raw_options):
            raise AdditionalCostError(
                "Alternative additional-cost options must be objects"
            )
        return cls(
            schema_version=value["schema_version"],
            kind=value["kind"],
            options=tuple(
                fixed_additional_cost_leaf(option)
                for option in raw_options
            ),
        )

    def to_descriptor(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "options": [
                option.to_descriptor() for option in self.options
            ],
        }


def fixed_alternative_additional_cost(
    value: Mapping[str, Any],
) -> FixedAlternativeAdditionalCost | None:
    if not isinstance(value, Mapping):
        raise AdditionalCostError("Additional costs must be objects")
    if value.get("kind") != ALTERNATIVE_ADDITIONAL_COST_KIND:
        return None
    return FixedAlternativeAdditionalCost.from_descriptor(value)


def fixed_life_payment_additional_cost(
    value: Mapping[str, Any],
) -> FixedLifePaymentAdditionalCost | None:
    if not isinstance(value, Mapping):
        raise AdditionalCostError("Additional costs must be objects")
    if value.get("kind") != FIXED_LIFE_PAYMENT_COST_KIND:
        return None
    return FixedLifePaymentAdditionalCost.from_descriptor(value)


def fixed_zone_change_additional_cost_capability(
    cost: FixedZoneChangeAdditionalCost,
) -> str:
    """Return the one reviewed capability owned by a typed zone-change leaf."""

    return {
        DISCARD_ONE_COST: "casting.additional_cost.zone_change.fixed_discard",
        SACRIFICE_ONE_COST: "casting.additional_cost.fixed_sacrifice",
        EXILE_ONE_FROM_GRAVEYARD_COST: (
            "casting.additional_cost.zone_change.fixed_exile"
        ),
        EXILE_ONE_FROM_BATTLEFIELD_COST: (
            "casting.additional_cost.zone_change.fixed_exile"
        ),
        EXILE_ONE_FROM_HAND_COST: (
            "casting.additional_cost.zone_change.fixed_exile"
        ),
        RETURN_ONE_TO_OWNER_HAND_COST: (
            "casting.additional_cost.zone_change.fixed_return_to_owner_hand"
        ),
    }[cost.operation]


def fixed_life_payment_additional_cost_node_capabilities(
    *, cost_schema: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """Recognize exactly one mandatory fixed life casting cost."""

    if not isinstance(cost_schema, Mapping) or set(cost_schema) != {
        "additional_costs"
    }:
        return ()
    raw_costs = cost_schema.get("additional_costs")
    if not isinstance(raw_costs, list) or len(raw_costs) != 1:
        return ()
    if isinstance(raw_costs[0], Mapping) and raw_costs[0].get('kind') == 'announced_x_life':
        try:
            AnnouncedXLifeAdditionalCost.from_descriptor(raw_costs[0])
        except (AdditionalCostError, TypeError):
            return ()
        return (ANNOUNCED_X_LIFE_CAPABILITY, 'casting.additional_cost.fixed_life_payment')
    try:
        FixedLifePaymentAdditionalCost.from_descriptor(raw_costs[0])
    except (AdditionalCostError, TypeError):
        return ()
    return ("casting.additional_cost.fixed_life_payment",)


def fixed_alternative_additional_cost_node_capabilities(
    *, cost_schema: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """Recognize one binary choice and every typed leaf it can commit."""

    if not isinstance(cost_schema, Mapping) or set(cost_schema) != {
        "additional_costs"
    }:
        return ()
    raw_costs = cost_schema.get("additional_costs")
    if not isinstance(raw_costs, list) or len(raw_costs) != 1:
        return ()
    try:
        cost = FixedAlternativeAdditionalCost.from_descriptor(raw_costs[0])
    except (AdditionalCostError, TypeError):
        return ()
    capabilities = {"casting.additional_cost.fixed_alternative"}
    for option in cost.options:
        if isinstance(option, FixedZoneChangeAdditionalCost):
            capabilities.add(
                fixed_zone_change_additional_cost_capability(option)
            )
        elif isinstance(option, FixedLifePaymentAdditionalCost):
            capabilities.add("casting.additional_cost.fixed_life_payment")
        elif isinstance(option,FixedRevealAdditionalCost):
            capabilities.add(REVEAL_ADDITIONAL_COST_CAPABILITY)
        elif isinstance(option,FixedTapAdditionalCost):
            capabilities.add(TAP_ADDITIONAL_COST_CAPABILITY)
        elif not isinstance(option, FixedManaPaymentAdditionalCost):
            return ()
    return tuple(sorted(capabilities))


def fixed_additional_cost_option_label(
    cost: FixedAdditionalCostLeaf,
) -> str:
    """Describe typed cost data for presentation without granting authority."""

    if isinstance(cost,FixedRevealAdditionalCost):return f'Reveal one {cost.subtype} card from your hand'
    if isinstance(cost,FixedTapAdditionalCost):return f'Tap {cost.count} matching untapped permanent(s)'
    if isinstance(cost, FixedManaPaymentAdditionalCost):
        requirements = cost.requirements_dict
        symbols = (
            ([f"{{{requirements['GENERIC']}}}"] if requirements["GENERIC"] else [])
            + [
                f"{{{color}}}"
                for color in "WUBRGC"
                for _ in range(requirements[color])
            ]
        )
        return "Pay " + "".join(symbols)
    if isinstance(cost, FixedLifePaymentAdditionalCost):
        return f"Pay {cost.amount} life"
    return {
        DISCARD_ONE_COST: "Discard one matching card",
        SACRIFICE_ONE_COST: "Sacrifice one matching permanent",
        EXILE_ONE_FROM_GRAVEYARD_COST: "Exile one matching graveyard card",
        EXILE_ONE_FROM_BATTLEFIELD_COST: "Exile one matching permanent",
        EXILE_ONE_FROM_HAND_COST: "Exile one matching hand card",
        RETURN_ONE_TO_OWNER_HAND_COST: (
            "Return one matching permanent to its owner's hand"
        ),
    }[cost.operation]


__all__ = [
    "FixedAdditionalCostLeaf",
    "FixedAlternativeAdditionalCost",
    "FixedLifePaymentAdditionalCost",
    "FixedManaPaymentAdditionalCost",
    "fixed_additional_cost_leaf",
    "fixed_additional_cost_option_label",
    "fixed_alternative_additional_cost",
    "fixed_alternative_additional_cost_node_capabilities",
    "fixed_life_payment_additional_cost",
    "fixed_life_payment_additional_cost_node_capabilities",
    "fixed_zone_change_additional_cost_capability",
]
