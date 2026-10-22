from __future__ import annotations

"""Immutable static activation-cost predicates and ordered mana prices."""

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .object_predicate import ObjectQuerySpec
from .power_up_model import reduce_power_up_requirements
from .replacement.immutable import FrozenMap


ACTIVATION_GENERIC_COST_CAPABILITY = 'activation.mana_cost.static_generic'
ACTIVATION_GENERIC_COST_HANDLER = 'modification.activation-cost.fixed-generic.v1'
ACTIVATION_GENERIC_COST_EVENT = 'activation.mana_cost.modify'
MANA_KEYS = ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')


class ActivationManaCostData(Protocol):
    mana: Mapping[str, int]
    announced_x_symbols: int | None
    generic_reduction_per_legendary_creature: int


def base_activation_requirements(
    ability: ActivationManaCostData, *, legendary_creatures: int = 0, announced_x: int = 0,
) -> dict[str, int]:
    """Preserve the established base fee and standalone legendary discount."""
    result = {'GENERIC': int(ability.mana.get('GENERIC', 0))}
    if type(announced_x) is not int or announced_x < 0:
        raise ValueError('Announced activation X must be a nonnegative integer')
    if ability.announced_x_symbols is not None:
        result['GENERIC'] += ability.announced_x_symbols * announced_x
    for color in 'WUBRGC':
        result[color] = int(ability.mana.get(color, 0))
    if ability.generic_reduction_per_legendary_creature:
        reduction = ability.generic_reduction_per_legendary_creature * max(0, legendary_creatures)
        result['GENERIC'] = max(0, result['GENERIC'] - reduction)
    return result


@dataclass(frozen=True, slots=True)
class FixedActivationCostModifierSpec:
    generic_adjustment: int
    predicate: ObjectQuerySpec = ObjectQuerySpec()
    subject_relation: str = 'query'
    controller_relation: str = 'any'
    ability_scope: str = 'all'
    minimum_mana: int = 0
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported activation-cost modifier schema')
        if type(self.generic_adjustment) is not int or not self.generic_adjustment:
            raise ValueError('Activation-cost adjustments must be nonzero integers')
        if type(self.minimum_mana) is not int or self.minimum_mana not in (0, 1):
            raise ValueError('Activation-cost reductions support per-effect minima zero or one')
        if self.generic_adjustment > 0 and self.minimum_mana:
            raise ValueError('Cost increases do not impose a reduction minimum')
        if self.subject_relation not in {'query', 'any', 'enchanted_object'}:
            raise ValueError('Unsupported activation-cost source relation')
        if self.controller_relation not in {'any', 'source_controller', 'source_opponents'}:
            raise ValueError('Unsupported activation-cost controller relation')
        if self.ability_scope not in {'all', 'nonmana','equip','cycling'}:
            raise ValueError('Unsupported activation-cost ability scope')
        if not isinstance(self.predicate, ObjectQuerySpec):
            raise ValueError('Activation-cost source predicates must be typed')
        if self.subject_relation == 'any':
            if self.predicate != ObjectQuerySpec():
                raise ValueError('All-zone activation modifiers reserve their object predicate')
            return
        predicate = self.predicate
        closed = ObjectQuerySpec(zones=('battlefield',),
            types_all=predicate.types_all, types_any=predicate.types_any,
            excluded_types=predicate.excluded_types, subtypes_all=predicate.subtypes_all,
            subtypes_any=predicate.subtypes_any, colors_all=predicate.colors_all,
            colors_any=predicate.colors_any, colorless=predicate.colorless, token=predicate.token)
        if predicate != closed:
            raise ValueError('Activation modifiers require a closed public battlefield predicate')

    def to_dict(self) -> dict[str, Any]:
        return {'schema_version': self.schema_version,
            'generic_adjustment': self.generic_adjustment, 'minimum_mana': self.minimum_mana,
            'subject_relation': self.subject_relation, 'controller_relation': self.controller_relation,
            'ability_scope': self.ability_scope, 'predicate': self.predicate.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> FixedActivationCostModifierSpec:
        fields = {'schema_version', 'generic_adjustment', 'minimum_mana',
            'subject_relation', 'controller_relation', 'ability_scope', 'predicate'}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError('Activation-cost modifier fields are closed')
        return cls(**{**dict(value), 'predicate': ObjectQuerySpec.from_dict(value['predicate'])})


@dataclass(frozen=True, slots=True)
class ActivationManaReduction:
    mana: FrozenMap
    minimum_mana: int = 0

    def __post_init__(self) -> None:
        mana = reduce_power_up_requirements(self.mana, {})
        if type(self.minimum_mana) is not int or self.minimum_mana not in (0, 1):
            raise ValueError('Mana reduction minimum must be zero or one')
        if self.minimum_mana and any(mana[key] for key in MANA_KEYS[1:]):
            raise ValueError('Minimum-one reductions must reduce only generic mana')
        object.__setattr__(self, 'mana', mana)


@dataclass(frozen=True, slots=True)
class ActivationCostPrice:
    order: str
    requirements: FrozenMap


def _reduce_price(requirements, reduction: ActivationManaReduction, snow_payment: int):
    before_generic = requirements['GENERIC']
    other_mana = snow_payment + sum(requirements[key] for key in MANA_KEYS[1:])
    price = dict(reduce_power_up_requirements(requirements, reduction.mana))
    minimum_generic = max(0, reduction.minimum_mana - other_mana)
    price['GENERIC'] = max(price['GENERIC'], min(before_generic, minimum_generic))
    return FrozenMap(price)


def activation_cost_prices(
    requirements: Mapping[str, int],
    modifiers: Sequence[FixedActivationCostModifierSpec],
    *,
    own_reductions: Sequence[ActivationManaReduction] = (),
    snow_payment: int = 0,
) -> tuple[ActivationCostPrice, ...]:
    """Add taxes, then retain each legal price from the two reducer group orders.

    For flat generic minima zero/one and ordinary unrestricted mana reductions,
    the order can change only whether the final generic component reaches zero
    or stops at one. Each group order is executable; equivalent prices collapse.
    """
    if type(snow_payment) is not int or snow_payment < 0:
        raise ValueError('Snow payment must be a nonnegative integer')
    if any(not isinstance(value, FixedActivationCostModifierSpec) for value in modifiers):
        raise ValueError('Activation-cost modifiers must be typed')
    if any(not isinstance(value, ActivationManaReduction) for value in own_reductions):
        raise ValueError('Activation mana reductions must be typed')
    base = dict(reduce_power_up_requirements(requirements, {}))
    base['GENERIC'] += sum(max(0, spec.generic_adjustment) for spec in modifiers)
    reductions = tuple(own_reductions) + tuple(
        ActivationManaReduction(FrozenMap({'GENERIC': -spec.generic_adjustment}), spec.minimum_mana)
        for spec in modifiers if spec.generic_adjustment < 0)
    minimum = tuple(value for value in reductions if value.minimum_mana)
    unrestricted = tuple(value for value in reductions if not value.minimum_mana)
    result = []
    seen = set()
    for name, ordered in (('minimum_first', (*minimum, *unrestricted)),
            ('unrestricted_first', (*unrestricted, *minimum))):
        price = FrozenMap(base)
        for reduction in ordered:
            price = _reduce_price(price, reduction, snow_payment)
        identity = tuple(price[key] for key in MANA_KEYS)
        if identity not in seen:
            result.append(ActivationCostPrice(name, price))
            seen.add(identity)
    return tuple(result)
