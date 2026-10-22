from __future__ import annotations

"""Immutable prices for an intrinsic, complete-source graveyard casting method."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re

from ..additional_cost_vocabulary import (
    DISCARD_ONE_COST, SACRIFICE_ONE_COST, EXILE_ONE_FROM_GRAVEYARD_COST,
)
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from ..util import mana_cost_to_vector, stable_json
from .casting_additional_cost_groups import (
    FixedLifePaymentAdditionalCost, fixed_zone_change_additional_cost_capability,
)
from .casting_additional_costs import FixedZoneChangeAdditionalCost
from .fixed_payment_groups import fixed_life_zone_dependencies


PAID_GRAVEYARD_CAPABILITY = 'casting.zone.self_graveyard.fixed_payment'
PAID_GRAVEYARD_HANDLER = 'permission.cast.self-graveyard-fixed-payment.v1'
PAID_GRAVEYARD_KIND = 'self_graveyard_fixed_payment'
PAID_GRAVEYARD_CONTEXT = 'self_graveyard_fixed_payment'
PAID_GRAVEYARD_FINGERPRINT = 'self_graveyard_fixed_payment_fingerprint'
PAID_GRAVEYARD_BASE = 'self_graveyard_fixed_payment_base'
_ORDINARY_MANA = re.compile(r'(?:\{(?:[0-9]+|[WUBRGC])\})+')


def decode_paid_graveyard_costs(values):
    if not isinstance(values, (list, tuple)) or len(values) > 2:
        raise ValueError('Paid graveyard methods require at most two fixed resource prices')
    result = []
    for value in values:
        if not isinstance(value, Mapping):
            raise ValueError('Paid graveyard resource prices must be typed objects')
        raw = thaw_value(value)
        if raw.get('kind') == 'fixed_life_payment':
            result.append(FixedLifePaymentAdditionalCost.from_descriptor(raw))
            continue
        cost = FixedZoneChangeAdditionalCost.from_descriptor(raw)
        if cost.schema_version != 1 or cost.count_parameter is not None or cost.unblocked_attacker:
            raise ValueError('Paid graveyard resource selection must use a fixed price')
        if cost.operation == DISCARD_ONE_COST:
            allowed = (ObjectQuerySpec(zones=('hand',), owner='$actor', known_to_actor=True),)
        elif cost.operation == SACRIFICE_ONE_COST:
            allowed = (ObjectQuerySpec(zones=('battlefield',), controller='$actor',
                known_to_actor=True, types_all=('creature',)),
                ObjectQuerySpec(zones=('battlefield',), controller='$actor',
                known_to_actor=True, types_any=('artifact', 'creature')))
        elif cost.operation == EXILE_ONE_FROM_GRAVEYARD_COST:
            allowed = (ObjectQuerySpec(zones=('graveyard',), owner='$actor',
                known_to_actor=True, types_all=('creature',)),)
        else:
            raise ValueError('Paid graveyard method has an unrepresented resource operation')
        if cost.predicate not in allowed:
            raise ValueError('Paid graveyard method has an unrepresented resource qualifier')
        result.append(cost)
    if len(result) == 2:
        fixed_life_zone_dependencies(values)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PaidGraveyardCastSpec:
    mana_cost: str | None = None
    additional_costs: tuple[FrozenMap, ...] = ()
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported paid graveyard method version')
        if self.mana_cost is not None:
            if type(self.mana_cost) is not str or _ORDINARY_MANA.fullmatch(self.mana_cost) is None:
                raise ValueError('Paid graveyard replacement mana must be an ordinary fixed price')
            _, complex_symbols = mana_cost_to_vector(self.mana_cost)
            if complex_symbols:
                raise ValueError('Paid graveyard replacement mana cannot contain variable symbols')
        if not isinstance(self.additional_costs, tuple) or any(
                not isinstance(value, FrozenMap) for value in self.additional_costs):
            raise ValueError('Paid graveyard resource prices must be immutable')
        decode_paid_graveyard_costs(self.additional_costs)
        if self.mana_cost is None and not self.additional_costs:
            raise ValueError('Paid graveyard permission requires its printed price contract')

    def to_dict(self):
        return {'schema_version': self.schema_version, 'kind': PAID_GRAVEYARD_KIND,
            'mana_cost': self.mana_cost, 'additional_costs': [thaw_value(v) for v in self.additional_costs]}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping)
                or set(value) != {'schema_version', 'kind', 'mana_cost', 'additional_costs'}
                or value['kind'] != PAID_GRAVEYARD_KIND
                or not isinstance(value['additional_costs'], (list, tuple))
                or any(not isinstance(v, Mapping) for v in value['additional_costs'])):
            raise ValueError('Paid graveyard methods use one closed typed schema')
        return cls(value['mana_cost'], tuple(FrozenMap(v) for v in value['additional_costs']), value['schema_version'])

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    @property
    def option_id(self):
        return 'graveyard-fixed-payment:' + self.fingerprint

    def capabilities(self):
        required = {PAID_GRAVEYARD_CAPABILITY, 'casting.zone.self_public_repeatable'}
        for cost in decode_paid_graveyard_costs(self.additional_costs):
            if isinstance(cost, FixedLifePaymentAdditionalCost):
                required.add('casting.additional_cost.fixed_life_payment')
            else:
                required.add(fixed_zone_change_additional_cost_capability(cost))
        if len(self.additional_costs) == 2:
            required.update(fixed_life_zone_dependencies(self.additional_costs))
        return tuple(sorted(required))

    def cost_option(self, printed):
        alternate_base = (self.mana_cost is None and printed.get('kind') == 'alternate'
            and isinstance(printed.get('battlefield_alternative_cost'), Mapping))
        if printed.get('id') != 'normal' and not alternate_base:
            raise ValueError('Paid graveyard method must start from its printed normal cost branch')
        option = deepcopy(dict(printed))
        option.update(id=str(printed['id']) + '+' + self.option_id if alternate_base else self.option_id,
            kind='alternate' if alternate_base or self.mana_cost is not None else 'zone_cast', label='Cast from graveyard',
            source_zone='graveyard', base_cost_option=str(printed['id']),
            **{PAID_GRAVEYARD_CONTEXT: self.to_dict(), PAID_GRAVEYARD_FINGERPRINT: self.fingerprint})
        option[PAID_GRAVEYARD_BASE] = str(printed['id'])
        if self.mana_cost is not None:
            option['requirements'] = mana_cost_to_vector(self.mana_cost)[0]
        option['_additional_option_costs'] = [*option.get('_additional_option_costs', ()),
            *(thaw_value(value) for value in self.additional_costs)]
        return option


def compiled_paid_graveyard_specs(host, seat, card):
    from .graveyard_payment_sources import compiled_graveyard_payment_specs
    return tuple(sorted(compiled_graveyard_payment_specs(host, seat, card, PAID_GRAVEYARD_HANDLER),
        key=lambda spec: spec.fingerprint))


def paid_graveyard_base_options(host, seat, card, printed, *,
        cast_without_mana, force_without_mana_cost, suppress_source_costs):
    if suppress_source_costs:
        return []
    return [spec.cost_option(option) for spec in compiled_paid_graveyard_specs(host, seat, card)
        if not ((cast_without_mana or force_without_mana_cost) and spec.mana_cost is not None)
        for option in printed if option.get('id') == 'normal']


def validate_paid_graveyard_option(host, seat, card, option, *, origin):
    raw = option.get(PAID_GRAVEYARD_CONTEXT)
    if raw is None:
        if 'graveyard-fixed-payment:' in str(option.get('id', '')):
            raise ValueError('Paid graveyard method is missing its sealed payment')
        return
    spec = PaidGraveyardCastSpec.from_dict(raw)
    base = option.get(PAID_GRAVEYARD_BASE)
    valid_id = base == 'normal' and option.get('id') == spec.option_id
    if base != 'normal' and spec.mana_cost is None:
        from .casting.global_alternatives import is_battlefield_optional_cost_option
        valid_id = base == option.get('base_cost_option') and is_battlefield_optional_cost_option(option, spec.option_id)
    if (origin != 'graveyard' or option.get('source_zone') != 'graveyard' or not valid_id
            or option.get(PAID_GRAVEYARD_FINGERPRINT) != spec.fingerprint
            or spec not in compiled_paid_graveyard_specs(host, seat, card)
            or list(option.get('additional_costs', ())) != spec.to_dict()['additional_costs']):
        raise ValueError('Paid graveyard source or payment contract changed before costs')


def paid_graveyard_runtime_dependencies(program):
    required = set()
    def visit(value):
        if isinstance(value, Mapping):
            if value.get('kind') == PAID_GRAVEYARD_KIND:
                required.update(PaidGraveyardCastSpec.from_dict(value).capabilities())
            costs = value.get('additional_costs')
            if (isinstance(costs, (list, tuple)) and len(costs) == 2
                    and isinstance(costs[0], Mapping) and costs[0].get('kind') == 'fixed_life_payment'
                    and isinstance(costs[1], Mapping) and costs[1].get('kind') == 'zone_change'):
                required.update(fixed_life_zone_dependencies(costs))
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit((program.effects, program.handlers, program.cost_schema))
    return tuple(sorted(required))
