from __future__ import annotations

"""Closed fixed color choices for mana produced during resolution."""

from dataclasses import dataclass
from typing import Any, Mapping

RESOLUTION_MANA_CHOICE_CAPABILITY='mana.production.fixed_resolution_choice'
MANA_COLORS=tuple('WUBRG')
RESTRICTED_RESOLUTION_MANA_VERSION = 5


@dataclass(frozen=True,slots=True)
class ResolutionManaChoiceSpec:
    selection: str
    amount: int
    colors: tuple[str, ...] = MANA_COLORS

    def __post_init__(self):
        if type(self.selection) is not str or self.selection not in {'one_color','combination'} or type(self.amount) is not int or not 1<=self.amount<=10:
            raise ValueError('Resolution mana choice requires a closed color selection and positive fixed amount')
        if self.colors != MANA_COLORS and (self.selection != "one_color" or self.amount != 1
                or not isinstance(self.colors, tuple) or len(self.colors) != 2
                or any(type(color) is not str or color not in MANA_COLORS for color in self.colors)
                or self.colors != tuple(sorted(set(self.colors)))):
            raise ValueError("Restricted resolution mana requires a canonical pair and one mana")

    def effect(self):
        result = {'op':'choose_mana','schema_version':2,'player':'$controller','source':'$source',
                  'selection':self.selection,'amount':self.amount}
        if self.colors != MANA_COLORS:
            result.update(schema_version=RESTRICTED_RESOLUTION_MANA_VERSION, colors=list(self.colors))
        return result

    @classmethod
    def from_effect(cls,value:Mapping[str,Any],*,continuation:bool=False):
        if not isinstance(value, Mapping):
            raise ValueError('Fixed resolution mana choice fields are malformed')
        version = value.get("schema_version")
        fields={'op','schema_version','player','source','selection','amount'}
        if continuation:fields|={'_choice_actor','_source_ref'}
        if version == RESTRICTED_RESOLUTION_MANA_VERSION:
            fields.add("colors")
        if (set(value)!=fields or value['op']!='choose_mana' or type(version) is not int
                or version not in (2, RESTRICTED_RESOLUTION_MANA_VERSION)
                or any(type(value[k]) is not str or not value[k] for k in ('player','source'))):
            raise ValueError('Fixed resolution mana choice fields are malformed')
        colors = value.get("colors", MANA_COLORS)
        if not isinstance(colors, (list, tuple)) or version == RESTRICTED_RESOLUTION_MANA_VERSION and len(colors) != 2:
            raise ValueError("Restricted resolution mana requires its two declared colors")
        return cls(value['selection'],value['amount'],tuple(colors))


def resolution_mana_choice_dependencies(value:Any)->tuple[str,...]:
    if not isinstance(value,Mapping) or value.get('op')!='choose_mana' or not ({'schema_version','selection','source'}&set(value)):
        return ()
    from .variable_resolution_mana_model import (
        OPTIONAL_TARGET_MANA_CHOICE_VERSION, VariableResolutionManaChoiceSpec, VARIABLE_RESOLUTION_MANA_CAPABILITY,
    )
    if value.get('schema_version') in {3, OPTIONAL_TARGET_MANA_CHOICE_VERSION}:
        VariableResolutionManaChoiceSpec.from_effect(value)
        return (RESOLUTION_MANA_CHOICE_CAPABILITY,VARIABLE_RESOLUTION_MANA_CAPABILITY)
    ResolutionManaChoiceSpec.from_effect(value)
    return (RESOLUTION_MANA_CHOICE_CAPABILITY,)
