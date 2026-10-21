from __future__ import annotations

"""Closed typed nonmana prices for the existing Buyback lifecycle."""

from typing import Mapping

from ..additional_cost_vocabulary import DISCARD_ONE_COST, SACRIFICE_ONE_COST
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .casting_additional_costs import AdditionalCostError, FixedZoneChangeAdditionalCost
from .casting_additional_cost_groups import (
    FixedLifePaymentAdditionalCost, fixed_zone_change_additional_cost_capability,
)
from .random_discard_additional_cost import (
    FixedRandomDiscardAdditionalCost, RANDOM_SPELL_COST_KIND, RANDOM_SPELL_COST_CAPABILITY,
)

NONMANA_BUYBACK_CAPABILITY = 'casting.buyback.fixed_nonmana'
LIFE_RANDOM_COST_CAPABILITY = 'casting.additional_cost.fixed_life_random'
_BASIC_TYPES = ('plains', 'island', 'swamp', 'mountain', 'forest')


def decode_nonmana_buyback_costs(values):
    if not isinstance(values, (tuple, list)) or not 1 <= len(values) <= 2:
        raise AdditionalCostError('Nonmana Buyback requires one closed price or life plus random discard')
    result = []
    for value in values:
        if not isinstance(value, Mapping):
            raise AdditionalCostError('Nonmana Buyback prices must be typed objects')
        raw = thaw_value(value)
        if raw.get('kind') == 'fixed_life_payment':
            result.append(FixedLifePaymentAdditionalCost.from_descriptor(raw))
        elif raw.get('kind') == RANDOM_SPELL_COST_KIND:
            result.append(FixedRandomDiscardAdditionalCost.from_descriptor(raw))
        else:
            cost = FixedZoneChangeAdditionalCost.from_descriptor(raw)
            if cost.schema_version != 1 or cost.count_parameter is not None or cost.unblocked_attacker:
                raise AdditionalCostError('Nonmana Buyback zone price must be fixed')
            if cost.operation == DISCARD_ONE_COST:
                expected = ObjectQuerySpec(zones=('hand',), owner='$actor', known_to_actor=True)
            elif cost.operation == SACRIFICE_ONE_COST:
                subtypes = cost.predicate.subtypes_all
                if subtypes and (len(subtypes) != 1 or subtypes[0] not in _BASIC_TYPES):
                    raise AdditionalCostError('Buyback sacrifice subtype is outside the fixed basic-land family')
                expected = ObjectQuerySpec(zones=('battlefield',), controller='$actor', known_to_actor=True,
                    types_all=('land',), subtypes_all=subtypes)
            else:
                raise AdditionalCostError('Nonmana Buyback zone operation is unsupported')
            if cost.predicate != expected:
                raise AdditionalCostError('Nonmana Buyback price has an unsupported qualifier')
            result.append(cost)
    if len(result) == 2 and not (
            isinstance(result[0], FixedLifePaymentAdditionalCost)
            and isinstance(result[1], FixedRandomDiscardAdditionalCost)):
        raise AdditionalCostError('Composite nonmana Buyback requires life followed by random discard')
    return tuple(result)


def nonmana_buyback_cost_dependencies(values):
    costs = decode_nonmana_buyback_costs(values)
    required = {NONMANA_BUYBACK_CAPABILITY}
    for cost in costs:
        if isinstance(cost, FixedZoneChangeAdditionalCost):
            required.add(fixed_zone_change_additional_cost_capability(cost))
        elif isinstance(cost, FixedLifePaymentAdditionalCost):
            required.add('casting.additional_cost.fixed_life_payment')
        else:
            required.update((RANDOM_SPELL_COST_CAPABILITY, 'casting.additional_cost.zone_change.fixed_discard', 'zone.discard.fixed_random'))
    if len(costs) == 2:required.add(LIFE_RANDOM_COST_CAPABILITY)
    return tuple(sorted(required))


def validate_nonmana_buyback_spec(spec):
    if spec.kind.value != 'buyback' or spec.schema_version != 5:
        raise AdditionalCostError('Only the typed Buyback variant carries nonmana lifecycle prices')
    if spec.counter_count is not None or spec.exile_count is not None:
        raise AdditionalCostError('Nonmana Buyback has no zone-cast designation counts')
    if not isinstance(spec.cost_text, str) or not spec.cost_text:
        raise AdditionalCostError('Nonmana Buyback requires a display price')
    if not isinstance(spec.mana_cost, FrozenMap) or dict(spec.mana_cost) != {k: 0 for k in ('GENERIC', 'W', 'U', 'B', 'R', 'G', 'C')}:
        raise AdditionalCostError('Nonmana Buyback cannot carry an additional mana price')
    if not isinstance(spec.additional_costs, tuple) or any(not isinstance(cost, FrozenMap) for cost in spec.additional_costs):
        raise AdditionalCostError('Nonmana Buyback prices must be immutable')
    decode_nonmana_buyback_costs(spec.additional_costs)


def nonmana_buyback_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('schema_version') == 5 and value.get('kind') == 'buyback':
                from ..cast_lifecycles import FixedCastLifecycleSpec
                spec = FixedCastLifecycleSpec.from_dict(value)
                required.update(nonmana_buyback_cost_dependencies(spec.additional_costs))
            for child in value.values():visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:visit(child)
    visit((program.handlers, program.cost_schema))
    return tuple(sorted(required))
