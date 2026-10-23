from __future__ import annotations

"""Closed fixed color choices for mana produced during resolution."""

from dataclasses import dataclass
from typing import Any, Mapping

RESOLUTION_MANA_CHOICE_CAPABILITY='mana.production.fixed_resolution_choice'
MANA_COLORS=tuple('WUBRG')


@dataclass(frozen=True,slots=True)
class ResolutionManaChoiceSpec:
    selection: str
    amount: int

    def __post_init__(self):
        if type(self.selection) is not str or self.selection not in {'one_color','combination'} or type(self.amount) is not int or not 1<=self.amount<=10:
            raise ValueError('Resolution mana choice requires a closed color selection and positive fixed amount')

    def effect(self):
        return {'op':'choose_mana','schema_version':2,'player':'$controller','source':'$source',
            'selection':self.selection,'amount':self.amount}

    @classmethod
    def from_effect(cls,value:Mapping[str,Any],*,continuation:bool=False):
        fields={'op','schema_version','player','source','selection','amount'}
        if continuation:fields|={'_choice_actor','_source_ref'}
        if (not isinstance(value,Mapping) or set(value)!=fields or value['op']!='choose_mana'
                or type(value['schema_version']) is not int or value['schema_version']!=2
                or any(type(value[k]) is not str or not value[k] for k in ('player','source'))):
            raise ValueError('Fixed resolution mana choice fields are malformed')
        return cls(value['selection'],value['amount'])


def resolution_mana_choice_dependencies(value:Any)->tuple[str,...]:
    if not isinstance(value,Mapping) or value.get('op')!='choose_mana' or not ({'schema_version','selection','source'}&set(value)):
        return ()
    ResolutionManaChoiceSpec.from_effect(value)
    return (RESOLUTION_MANA_CHOICE_CAPABILITY,)
