"""Immutable public mana-value bounds; evaluation remains amount-owned."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .query_effect_amount_model import CastXAmountSpec
    from .scalar_effect_amount_model import ScalarEffectAmountSpec

MANA_VALUE_FILTER_CAPABILITY='state_query.permanent.mana_value_filter'
MANA_VALUE_FILTER_MECHANIC='mana-value-permanent-filter'
CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY = 'state_query.permanent.cast_spell_mana_value_filter'
CAST_SPELL_MANA_VALUE_FILTER_MECHANIC = 'cast-spell-mana-value-permanent-filter'


class ManaValueComparison(str,Enum):
    EQUAL='equal'
    AT_MOST='at_most'
    AT_LEAST='at_least'


@dataclass(frozen=True,slots=True)
class ManaValueFilter:
    comparison: ManaValueComparison
    value: int | ScalarEffectAmountSpec | CastXAmountSpec
    schema_version: int=1

    def __post_init__(self):
        from .query_effect_amount_model import CastXAmountSpec
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
        if type(self.schema_version)is not int or self.schema_version!=1 or not isinstance(self.comparison,ManaValueComparison):
            raise ValueError('Mana-value filters require their closed version and comparison')
        if type(self.value)is int:
            if self.value<0:raise ValueError('Mana-value bounds cannot be negative')
        elif isinstance(self.value,CastXAmountSpec):
            if self.value!=CastXAmountSpec():raise ValueError('Mana-value filters require the unchanged announced X')
        elif isinstance(self.value, ScalarEffectAmountSpec):
            source_count = (
                self.value.schema_version == 2 and self.value.origin is ScalarAmountOrigin.SOURCE
                and self.value.counter_name is not None and self.value.coefficient == 1 and self.value.binding_id is None
            )
            cast_value = self.comparison is ManaValueComparison.EQUAL and self.value == cast_spell_mana_value_operand()
            if not (source_count or cast_value):
                raise ValueError("Mana-value filters require one named source counter or exact committed cast value")
        else:raise ValueError('Mana-value bounds require a fixed integer or represented amount')

    def to_dict(self):
        return {'schema_version':1,'comparison':self.comparison.value,
                'value':self.value if type(self.value)is int else self.value.to_dict()}

    @classmethod
    def from_dict(cls,value):
        from .query_effect_amount_model import CastXAmountSpec,CAST_X_AMOUNT_KIND
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
        if not isinstance(value,Mapping) or set(value)!={'schema_version','comparison','value'}:
            raise ValueError('Mana-value filters have a closed schema')
        amount=value['value']
        if isinstance(amount,Mapping):
            if amount.get('kind')==CAST_X_AMOUNT_KIND:amount=CastXAmountSpec.from_dict(amount)
            elif amount.get('kind')==SCALAR_AMOUNT_KIND:amount=ScalarEffectAmountSpec.from_dict(amount)
            else:raise ValueError('Unknown mana-value amount instruction')
        return cls(ManaValueComparison(value['comparison']),amount,value['schema_version'])

    @property
    def uses_cast_spell_mana_value(self):
        from .scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

        return isinstance(self.value, ScalarEffectAmountSpec) and self.value.origin is ScalarAmountOrigin.CAST_SPELL_MANA_VALUE

    @property
    def capability_dependencies(self):
        from .query_effect_amount_model import CastXAmountSpec,DECLARED_AMOUNT_CAPABILITY
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_CAPABILITY
        from .cast_event_amounts import CAST_MANA_VALUE_CAPABILITY
        cast = (CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY, CAST_MANA_VALUE_CAPABILITY) if self.uses_cast_spell_mana_value else ()
        return (MANA_VALUE_FILTER_CAPABILITY, *cast,
            *((DECLARED_AMOUNT_CAPABILITY,) if isinstance(self.value,CastXAmountSpec) else ()),
            *((SCALAR_AMOUNT_CAPABILITY,) if isinstance(self.value,ScalarEffectAmountSpec) else ()))

    @property
    def mechanic_ids(self):
        from .query_effect_amount_model import CastXAmountSpec
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_MECHANIC
        from .cast_event_amounts import CAST_MANA_VALUE_MECHANIC
        cast = (CAST_SPELL_MANA_VALUE_FILTER_MECHANIC, CAST_MANA_VALUE_MECHANIC) if self.uses_cast_spell_mana_value else ()
        return (MANA_VALUE_FILTER_MECHANIC, *cast,
            *(('declared-effect-amount',) if isinstance(self.value,CastXAmountSpec) else ()),
            *((SCALAR_AMOUNT_MECHANIC,) if isinstance(self.value,ScalarEffectAmountSpec) else ()))

    @property
    def resolved_value(self):
        if type(self.value)is not int:raise ValueError('Mana-value amount must resolve before selecting permanents')
        return self.value

    def matches(self,mana_value):
        bound=self.resolved_value
        if type(mana_value)not in {int,float} or not math.isfinite(mana_value) or mana_value<0:
            raise ValueError('Mana-value facts must be finite nonnegative numbers')
        if self.comparison is ManaValueComparison.EQUAL:return mana_value==bound
        if self.comparison is ManaValueComparison.AT_MOST:return mana_value<=bound
        return mana_value>=bound


def cast_spell_mana_value_operand():
    from .scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return ScalarEffectAmountSpec(ScalarAmountOrigin.CAST_SPELL_MANA_VALUE, characteristic="mana_value", schema_version=8)
