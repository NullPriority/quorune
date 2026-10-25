from __future__ import annotations

"""A sealed optional Kicker branch over existing additional-cost leaves."""

from dataclasses import dataclass
from collections.abc import Mapping
import hashlib,re
from .replacement.immutable import FrozenMap,freeze_value,thaw_value
from .util import stable_json

NONMANA_KICKER_CAPABILITY='casting.kicker.fixed_nonmana'
NONMANA_KICKER_HANDLER='casting.kicker.fixed-nonmana.v1'


@dataclass(frozen=True,slots=True)
class FixedNonmanaKickerSpec:
    ability_id: str
    line_index: int
    oracle_line: str
    cost_text: str
    mana_cost: FrozenMap
    additional_costs: tuple
    schema_version: int=1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version!=1 or type(self.ability_id) is not str
                or re.fullmatch(r'ab[1-9]\d*',self.ability_id) is None or type(self.line_index) is not int or self.line_index<0
                or type(self.oracle_line) is not str or not self.oracle_line or type(self.cost_text) is not str or not self.cost_text):
            raise ValueError('Fixed nonmana Kicker identity is malformed')
        if not isinstance(self.mana_cost,Mapping):raise ValueError('Kicker mana vector is malformed')
        mana=dict(self.mana_cost)
        if set(mana)!={'GENERIC','W','U','B','R','G','C'} or any(type(v) is not int or v<0 for v in mana.values()):
            raise ValueError('Kicker mana vector is malformed')
        if type(self.additional_costs) is not tuple or len(self.additional_costs)!=1:
            raise ValueError('Kicker requires one represented nonmana leaf')
        from .rules.casting_additional_cost_groups import fixed_additional_cost_leaf,FixedLifePaymentAdditionalCost
        from .rules.casting_additional_costs import FixedZoneChangeAdditionalCost
        from .rules.tap_additional_cost import FixedTapAdditionalCost
        leaves=tuple(fixed_additional_cost_leaf(thaw_value(v)) for v in self.additional_costs)
        if any(not isinstance(v,(FixedLifePaymentAdditionalCost,FixedZoneChangeAdditionalCost,FixedTapAdditionalCost)) for v in leaves):
            raise ValueError('Kicker leaf is outside the represented nonmana family')
        if any(isinstance(v,FixedZoneChangeAdditionalCost) and v.operation not in {'discard_one','sacrifice_one','return_one_to_owner_hand'} for v in leaves):
            raise ValueError('Kicker zone operation is outside the represented family')
        object.__setattr__(self,'mana_cost',FrozenMap(mana))
        object.__setattr__(self,'additional_costs',tuple(freeze_value(thaw_value(v)) for v in self.additional_costs))

    def to_dict(self):
        return {'schema_version':1,'ability_id':self.ability_id,'line_index':self.line_index,
            'oracle_line':self.oracle_line,'cost_text':self.cost_text,'mana_cost':thaw_value(self.mana_cost),
            'additional_costs':[thaw_value(v) for v in self.additional_costs]}

    @classmethod
    def from_dict(cls,value):
        if not isinstance(value,Mapping) or set(value)!={'schema_version','ability_id','line_index','oracle_line','cost_text','mana_cost','additional_costs'}:
            raise ValueError('Nonmana Kicker fields are malformed')
        if type(value['additional_costs']) is not list:raise ValueError('Kicker leaves must be an array')
        return cls(value['ability_id'],value['line_index'],value['oracle_line'],value['cost_text'],value['mana_cost'],
            tuple(value['additional_costs']),value['schema_version'])

    @property
    def fingerprint(self):return hashlib.sha256(stable_json(self.to_dict()).encode('utf-8')).hexdigest()

    def cast_cost_option(self):
        return {'id':'kicked','kind':'kicker','label':'Kicker '+self.cost_text,'requirements':thaw_value(self.mana_cost),
            'kicker_fingerprint':self.fingerprint,'_additional_option_costs':[thaw_value(v) for v in self.additional_costs]}


def nonmana_kicker_dependencies(spec):
    from .rules.casting_additional_cost_groups import fixed_additional_cost_leaf,FixedLifePaymentAdditionalCost,fixed_zone_change_additional_cost_capability
    from .rules.casting_additional_costs import FixedZoneChangeAdditionalCost
    from .rules.tap_additional_cost import FixedTapAdditionalCost,TAP_ADDITIONAL_COST_CAPABILITY
    result={NONMANA_KICKER_CAPABILITY}
    for raw in spec.additional_costs:
        cost=fixed_additional_cost_leaf(thaw_value(raw))
        if isinstance(cost,FixedLifePaymentAdditionalCost):result.add('casting.additional_cost.fixed_life_payment')
        elif isinstance(cost,FixedZoneChangeAdditionalCost):result.add(fixed_zone_change_additional_cost_capability(cost))
        elif isinstance(cost,FixedTapAdditionalCost):result.add(TAP_ADDITIONAL_COST_CAPABILITY)
    return tuple(sorted(result))


def distinct_kicker_zone_costs(option,values):
    """Recognize a mandatory zone price followed by the sealed Kicker price."""
    from .rules.casting_additional_costs import FixedZoneChangeAdditionalCost
    if option.get('kind')!='kicker' or not option.get('kicker_fingerprint') or len(values)!=2:return False
    try:costs=tuple(FixedZoneChangeAdditionalCost.from_descriptor(v) for v in values)
    except (TypeError,ValueError):return False
    return (all(c.schema_version==1 and c.count_parameter is None and not c.unblocked_attacker for c in costs)
        and len({c.origin_zone for c in costs})==2 and len({c.choice_field for c in costs})==2)
