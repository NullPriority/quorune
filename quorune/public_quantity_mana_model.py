from __future__ import annotations

"""Closed public amount and color selection for mana production."""

from dataclasses import dataclass, replace
from typing import Any, Mapping
from .characteristic_fragments import CharacteristicQuantitySpec
from .scalar_effect_amount_model import ScalarEffectAmountSpec, ScalarAmountOrigin, SCALAR_AMOUNT_KIND
from .mana_restrictions import valid_mana_spend_restriction
from .characteristic_fragments import CharacteristicQuantityScope
from .query_effect_amount_model import PublicQueryAmountSpec

PUBLIC_QUANTITY_MANA_KIND = 'public_quantity_mana'
PUBLIC_QUANTITY_MANA_CAPABILITY = 'mana.production.public_quantity'


@dataclass(frozen=True, slots=True)
class PublicQuantityManaOutput:
    selection: str
    colors: tuple[str, ...]
    amount: CharacteristicQuantitySpec | ScalarEffectAmountSpec
    restriction: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError('Unsupported public quantity mana version')
        if type(self.selection) is not str or self.selection not in {'fixed','choose_one','combination'}:
            raise ValueError('Unsupported public quantity mana selection')
        colors=tuple(self.colors)
        if not colors or any(type(c) is not str or c not in 'WUBRGC' or len(c)!=1 for c in colors):
            raise ValueError('Public quantity mana requires canonical colors')
        if colors!=tuple(sorted(set(colors))) or (self.selection=='fixed' and len(colors)!=1):
            raise ValueError('Public quantity mana color selection is malformed')
        if self.selection!='fixed' and 'C' in colors:
            raise ValueError('Any-color selection cannot include colorless')
        if not isinstance(self.amount,(CharacteristicQuantitySpec,ScalarEffectAmountSpec)):
            raise ValueError('Public quantity mana requires an existing typed producer')
        if isinstance(self.amount,CharacteristicQuantitySpec) and self.amount.scope is not CharacteristicQuantityScope.SOURCE_COUNTER:
            PublicQueryAmountSpec(replace(self.amount,exclude_source=False))
        if isinstance(self.amount,ScalarEffectAmountSpec) and self.amount.origin not in {ScalarAmountOrigin.SOURCE,ScalarAmountOrigin.HISTORY}:
            raise ValueError('Target or event-relative mana amounts are outside this grammar')
        if isinstance(self.amount,ScalarEffectAmountSpec) and (self.amount.coefficient != 1 or self.amount.binding_id is not None):
            raise ValueError('Public quantity mana does not carry scalar arithmetic or declaration caches')
        if isinstance(self.amount,ScalarEffectAmountSpec) and self.amount.origin is ScalarAmountOrigin.SOURCE and self.amount.characteristic not in {'power','toughness'}:
            raise ValueError('Public quantity mana source fields are power or toughness')
        if self.restriction is not None and not valid_mana_spend_restriction(self.restriction):
            raise ValueError('Public quantity mana spend restriction is unsupported')
        object.__setattr__(self,'colors',colors)

    def to_dict(self) -> dict[str, Any]:
        return {'kind':PUBLIC_QUANTITY_MANA_KIND,'schema_version':self.schema_version,
            'selection':self.selection,'colors':list(self.colors),'amount':self.amount.to_dict(),'restriction':self.restriction}

    @classmethod
    def from_dict(cls, value: Mapping[str,Any]) -> PublicQuantityManaOutput:
        fields={'kind','schema_version','selection','colors','amount','restriction'}
        if not isinstance(value,Mapping) or set(value)!=fields or value['kind']!=PUBLIC_QUANTITY_MANA_KIND:
            raise ValueError('Public quantity mana fields are missing or unknown')
        if not isinstance(value['colors'],(list,tuple)) or not isinstance(value['amount'],Mapping):
            raise ValueError('Public quantity mana colors and amount are malformed')
        raw=value['amount']
        amount=ScalarEffectAmountSpec.from_dict(raw) if raw.get('kind')==SCALAR_AMOUNT_KIND else CharacteristicQuantitySpec.from_dict(raw)
        return cls(value['selection'],tuple(value['colors']),amount,value['restriction'],value['schema_version'])


def validate_dynamic_mana_output(ability):
    from .replacement.immutable import FrozenMap
    value=ability.dynamic_mana_output
    if value is None or value=='opponent_land_colors':
        return
    output=PublicQuantityManaOutput.from_dict(value)
    if not ability.mana_ability or ability.fixed_mana_outputs or ability.color_set_mana_output is not None:
        raise ValueError('Public quantity mana cannot mix competing output descriptors')
    if output.restriction != ability.mana_spend_restriction or ability.loyalty_delta is not None or ability.target_schema is not None:
        raise ValueError('Public quantity mana classification or restriction is inconsistent')
    object.__setattr__(ability,'dynamic_mana_output',FrozenMap(output.to_dict()))
