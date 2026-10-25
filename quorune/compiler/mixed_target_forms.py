from __future__ import annotations

"""Closed mixed type/subtype leaves for the existing target-form model."""

from dataclasses import fields
import re

from ..target_forms import TargetCharacteristicForm
from .creature_subtypes import canonical_creature_subtype


def _leaf(term,card_types,noncreature_subtypes):
    if term in card_types:return TargetCharacteristicForm(types_all=(term,))
    if term in noncreature_subtypes or canonical_creature_subtype(term)==term:
        return TargetCharacteristicForm(subtypes_any=(term,))
    raise ValueError('Mixed target alternatives require known permanent types or subtypes')


def _ordered(forms):
    return tuple(sorted(forms,key=lambda form:(not bool(form.types_all),form.types_all or form.subtypes_any)))


def mixed_permanent_target_fields(phrase,*,card_types,noncreature_subtypes):
    if re.fullmatch(r"[a-z][a-z'-]*(?:, [a-z][a-z'-]*)*(?:,? or [a-z][a-z'-]*)",phrase) is None:
        return None
    terms=tuple(re.split(r', (?:or )?| or ',phrase))
    if not 2<=len(terms)<=4 or len(set(terms))!=len(terms):return None
    try:forms=tuple(_leaf(term,card_types,noncreature_subtypes) for term in terms)
    except ValueError:return None
    if not any(form.types_all for form in forms) or not any(form.subtypes_any for form in forms):return None
    return {'characteristic_forms_any':_ordered(forms)}


def canonicalize_mixed_direct_forms(spec,*,card_types,noncreature_subtypes):
    forms=spec.characteristic_forms_any
    if not forms:
        if type(forms) is not tuple:raise ValueError('Empty mixed target alternatives must be an immutable tuple')
        return
    if not isinstance(forms,(tuple,list)) or not 2<=len(forms)<=4:
        raise ValueError('Mixed target alternatives require two through four typed leaves')
    for form in forms:
        if not isinstance(form,TargetCharacteristicForm):raise ValueError('Mixed target leaves must be typed characteristic forms')
        values=(*form.types_all,*form.subtypes_any,*form.supertypes_any)
        if len(values)!=1 or form!=_leaf(values[0],card_types,noncreature_subtypes):
            raise ValueError('Mixed target leaves require one unqualified type or subtype')
    if (len(set(forms))!=len(forms) or not any(form.types_all for form in forms)
            or not any(form.subtypes_any for form in forms)):
        raise ValueError('Mixed target alternatives require distinct type and subtype leaves')
    shared={'characteristic_forms_any','controller_relation','source_exclusion'}
    if any(getattr(spec,item.name)!=item.default for item in fields(spec) if item.name not in shared):
        raise ValueError('Mixed target alternatives only share controller and source-exclusion scope')
    object.__setattr__(spec,'characteristic_forms_any',_ordered(forms))
