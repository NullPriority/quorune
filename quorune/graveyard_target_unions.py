"""Typed disjunctions of card type, subtype and supertype in an owned graveyard."""
from __future__ import annotations
from dataclasses import dataclass,fields
from collections.abc import Mapping
import re

from .target_forms import HISTORIC_TARGET_FORMS, TargetCharacteristicForm
from .rules.graveyard_card_targets import OwnGraveyardCardTargetSpec,GraveyardCardTargetError
from .creature_subtypes import canonical_creature_subtype

CARD_TYPES=frozenset({'artifact','battle','creature','enchantment','instant','kindred','land','planeswalker','sorcery'})
SUPERTYPES=frozenset({'basic','legendary','snow'})
CARD_SUBTYPES=frozenset({'arcane','aura','equipment','food','vehicle'})
CAPABILITY='target.card.own_graveyard_mixed_forms'


def _leaf(word):
    if word in CARD_TYPES:return TargetCharacteristicForm(types_all=(word,))
    if word in SUPERTYPES:return TargetCharacteristicForm(supertypes_any=(word,))
    if word in CARD_SUBTYPES or canonical_creature_subtype(word)==word:
        return TargetCharacteristicForm(subtypes_any=(word,))
    raise GraveyardCardTargetError('Graveyard alternatives require known unqualified card qualities')


def _ordered(forms):
    return tuple(sorted(forms,key=lambda f:(f.types_all,f.subtypes_any,f.supertypes_any)))


@dataclass(frozen=True,slots=True)
class MixedOwnGraveyardTargetSpec(OwnGraveyardCardTargetSpec):
    characteristic_forms_any: tuple[TargetCharacteristicForm,...]=()

    def __post_init__(self):
        forms=self.characteristic_forms_any
        if not isinstance(forms,(list,tuple)) or not 2<=len(forms)<=4 or len(set(forms))!=len(forms):
            raise GraveyardCardTargetError('Mixed graveyard alternatives require two through four distinct leaves')
        historic = set(forms) == set(HISTORIC_TARGET_FORMS)
        kinds=set()
        for form in forms:
            if not isinstance(form,TargetCharacteristicForm):raise GraveyardCardTargetError('Mixed leaves must be typed')
            words=(*form.types_all,*form.subtypes_any,*form.supertypes_any)
            if not historic and (len(words)!=1 or form!=_leaf(words[0])):
                raise GraveyardCardTargetError('Mixed leaves must contain one canonical quality')
            kinds.add('type' if form.types_all else 'subtype' if form.subtypes_any else 'supertype')
        if len(kinds)<2:raise GraveyardCardTargetError('Homogeneous alternatives retain their existing owner')
        if any(getattr(self,f.name)!=f.default for f in fields(self)
                if f.name not in {'kind','characteristic_forms_any','mana_value_max'}):
            raise GraveyardCardTargetError('Mixed graveyard forms cannot carry unowned extra predicates')
        if self.kind is not None or self.mana_value_max is not None and (
                type(self.mana_value_max) is not int or not 0<=self.mana_value_max<=20):
            raise GraveyardCardTargetError('Mixed graveyard form kind or mana-value bound is unsupported')
        object.__setattr__(self,'characteristic_forms_any',_ordered(forms))

    @property
    def slug(self):
        return 'mixed-'+'-or-'.join((*f.types_all,*f.subtypes_any,*f.supertypes_any)[0]
            for f in self.characteristic_forms_any)

    def to_target_schema(self):
        value={'zones':['graveyard'],'categories':['card'],'owner_relation':'you','count':1,
            'characteristic_forms_any':[f.to_dict() for f in self.characteristic_forms_any]}
        if self.mana_value_max is not None:value['mana_value_max']=self.mana_value_max
        return value


def mixed_graveyard_target_from_schema(value):
    expected={'zones','categories','owner_relation','count','characteristic_forms_any'}
    if (not isinstance(value,Mapping) or set(value) not in (expected,expected|{'mana_value_max'})
            or value['zones']!=['graveyard'] or value['categories']!=['card']
            or value['owner_relation']!='you' or type(value['count']) is not int or value['count']!=1
            or not isinstance(value['characteristic_forms_any'],(list,tuple))):
        raise GraveyardCardTargetError('Mixed graveyard target header or fields are invalid')
    spec=MixedOwnGraveyardTargetSpec(None,mana_value_max=value.get('mana_value_max'),
        characteristic_forms_any=tuple(TargetCharacteristicForm.from_mapping(f) for f in value['characteristic_forms_any']))
    if spec.to_target_schema()!=value:raise GraveyardCardTargetError('Mixed graveyard target schema is not canonical')
    return spec


def mixed_graveyard_target(description):
    normalized=' '.join(description.casefold().split())
    if normalized == "historic":
        return MixedOwnGraveyardTargetSpec(None, characteristic_forms_any=HISTORIC_TARGET_FORMS)
    if re.fullmatch(r"[a-z][a-z'-]*(?:, [a-z][a-z'-]*)*(?:,? or [a-z][a-z'-]*)",normalized) is None:return None
    words=tuple(re.split(r', (?:or )?| or ',normalized))
    try:return MixedOwnGraveyardTargetSpec(None,characteristic_forms_any=tuple(_leaf(word) for word in words))
    except (TypeError,ValueError):return None


def mixed_graveyard_target_dependencies(schema):
    if not isinstance(schema,Mapping) or 'characteristic_forms_any' not in schema or schema.get('zones')!=['graveyard']:
        return ()
    mixed_graveyard_target_from_schema(schema)
    return (CAPABILITY,)
