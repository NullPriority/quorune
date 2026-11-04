from __future__ import annotations

"""Closed Incubate instructions and one replacement-capable creation intent."""

from collections.abc import Mapping
from dataclasses import dataclass

from .replacement.immutable import FrozenMap


INCUBATE_OPERATION = 'incubate'
INCUBATE_NEXT_OPERATION = 'incubate_next'
INCUBATE_MECHANIC = 'incubate'
INCUBATE_CAPABILITY = 'token.creation.incubate'
DEFINED_TOKEN_CAPABILITY = 'token.creation.defined_transforming'
INCUBATE_DEPENDENCIES = (INCUBATE_CAPABILITY, DEFINED_TOKEN_CAPABILITY,
    'counter.producer.effect_entry', 'counter.placement.quantity_replacement',
    'token.creation.quantity_replacement', 'token.creation.additional_replacement',
    'permanent.transform.face_change')


def validate_incubate_effect(value, *, resolved=False):
    if not isinstance(value,Mapping):
        raise ValueError('Incubate requires a closed instruction')
    staged = value.get('op') == INCUBATE_NEXT_OPERATION
    repeat_field = 'remaining' if staged else 'repetitions'
    fields = {'op','schema_version','controller','source','amount',repeat_field}
    if (set(value) != fields or value.get('op') not in {INCUBATE_OPERATION,INCUBATE_NEXT_OPERATION}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or any(type(value[field]) is not str or not value[field] or value[field].strip() != value[field]
                   for field in ('controller','source'))
            or type(value[repeat_field]) is not int
            or value[repeat_field] not in ((1,) if staged else (1,2))):
        raise ValueError('Incubate instruction identity or repetition count is malformed')
    amount = value['amount']
    if type(amount) is int:
        if amount < 0:raise ValueError('Incubate amount must be nonnegative')
    elif not (resolved or staged) and isinstance(amount,Mapping):
        from .query_effect_amount_model import (PublicQueryAmountSpec,CastXAmountSpec,
            PUBLIC_QUERY_AMOUNT_KIND,CAST_X_AMOUNT_KIND)
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,SCALAR_AMOUNT_KIND
        constructors = {PUBLIC_QUERY_AMOUNT_KIND:PublicQueryAmountSpec,
            CAST_X_AMOUNT_KIND:CastXAmountSpec,SCALAR_AMOUNT_KIND:ScalarEffectAmountSpec}
        constructor = constructors.get(amount.get('kind'))
        if constructor is None or constructor.from_dict(amount).coefficient < 0:
            raise ValueError('Incubate amount producer is unrepresented or negative')
    else:raise ValueError('Incubate amount must be resolved before creation')


@dataclass(frozen=True,slots=True)
class IncubateIntent:
    actor: str
    controller: str
    amount: int
    source_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap,...] = ()
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or any(type(value) is not str or not value or value.strip() != value
                       for value in (self.actor,self.controller,self.source_ref,self.reason))
                or self.actor != self.controller or type(self.amount) is not int or self.amount < 0):
            raise ValueError('Incubate intent requires its actor, controller, resolved amount and source')
        if not isinstance(self.replacement_selections,tuple):
            raise ValueError('Incubate replacement selections must be an immutable sequence')
        selections = []
        for selection in self.replacement_selections:
            if isinstance(selection,Mapping):selection = FrozenMap(selection)
            elif type(selection) is not str or not selection:
                raise ValueError('Incubate replacement selection is malformed')
            selections.append(selection)
        object.__setattr__(self,'replacement_selections',tuple(selections))

    def identity(self):
        return {'schema_version':1,'actor':self.actor,'controller':self.controller,
            'amount':self.amount,'source_ref':self.source_ref,'reason':self.reason}


def decode_incubate_intent_identity(value):
    fields = {'schema_version','actor','controller','amount','source_ref','reason'}
    if not isinstance(value,Mapping) or set(value) != fields:
        raise ValueError('Incubate intent identity has a closed schema')
    return IncubateIntent(**dict(value)).identity()


def incubate_payload_dependencies(value):
    if not isinstance(value,Mapping) or value.get('op') not in {INCUBATE_OPERATION,INCUBATE_NEXT_OPERATION}:
        return ()
    validate_incubate_effect(value)
    return INCUBATE_DEPENDENCIES
