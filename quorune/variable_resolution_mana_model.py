"""Version-three public-amount mana choices, distinct from legacy fixed choices."""
from dataclasses import dataclass
from collections.abc import Mapping
from .query_effect_amount_model import PublicQueryAmountSpec,PUBLIC_QUERY_AMOUNT_KIND
from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
from .replacement.immutable import FrozenMap,thaw_value

VARIABLE_RESOLUTION_MANA_CAPABILITY='mana.production.variable_resolution_choice'
MANA_COLORS=tuple('WUBRG')


@dataclass(frozen=True,slots=True)
class VariableResolutionManaChoiceSpec:
    selection: str
    colors: tuple[str,...]
    amount: object

    def __post_init__(self):
        if not isinstance(self.colors,(list,tuple)) or type(self.selection) is not str:
            raise ValueError('Variable resolution mana requires a typed color array and selection')
        object.__setattr__(self,'colors',tuple(self.colors))
        if (self.selection not in {'one_color','combination'} or not self.colors
                or self.colors!=tuple(sorted(set(self.colors))) or not set(self.colors)<=set(MANA_COLORS)):
            raise ValueError('Variable resolution mana requires a closed canonical color selection')
        if isinstance(self.amount,Mapping):
            if self.amount.get('kind')==PUBLIC_QUERY_AMOUNT_KIND:PublicQueryAmountSpec.from_dict(self.amount)
            elif self.amount.get('kind')==SCALAR_AMOUNT_KIND:ScalarEffectAmountSpec.from_dict(self.amount)
            else:raise ValueError('Variable resolution mana requires an existing public amount expression')
            object.__setattr__(self,'amount',FrozenMap(self.amount))
        elif type(self.amount) is not int or self.amount<0:
            raise ValueError('Resolved variable mana amount must be a nonnegative integer')

    def effect(self):
        return {'op':'choose_mana','schema_version':3,'player':'$controller','source':'$source',
            'selection':self.selection,'colors':list(self.colors),'amount':thaw_value(self.amount) if isinstance(self.amount,Mapping) else self.amount}

    @classmethod
    def from_effect(cls,value,*,continuation=False,resolved=False):
        fields={'op','schema_version','player','source','selection','colors','amount'}
        if continuation:fields|={'_choice_actor','_source_ref'}
        if (not isinstance(value,Mapping) or set(value)!=fields or value['op']!='choose_mana'
                or type(value['schema_version']) is not int or value['schema_version']!=3
                or any(type(value[k]) is not str or not value[k] for k in ('player','source'))):
            raise ValueError('Variable resolution mana choice has a closed version-three shape')
        spec=cls(value['selection'],value['colors'],value['amount'])
        if (resolved and type(spec.amount) is not int) or (not resolved and not isinstance(spec.amount,Mapping)):
            raise ValueError('Variable mana expression and sealed resolution amount cannot be interchanged')
        return spec
