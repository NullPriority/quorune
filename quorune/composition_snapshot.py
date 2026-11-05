from __future__ import annotations

"""Immutable authoritative object data for future composition transactions."""

from collections.abc import Mapping
from dataclasses import dataclass

from .continuous_effect_model import ContinuousObjectIdentity
from .replacement.immutable import FrozenMap,thaw_value
from .permanent_composition import _validate_identity


def _text(value):
    return type(value) is str and bool(value) and value.strip()==value


@dataclass(frozen=True,slots=True)
class CompositionObjectSnapshot:
    card_state: FrozenMap
    copiable_values: FrozenMap
    controller: str
    stack_ref: str | None=None
    stack_kind: str | None=None
    schema_version: int=1

    def __post_init__(self):
        from .model import CardInstance
        if (type(self.schema_version) is not int or self.schema_version!=1
                or not isinstance(self.card_state,Mapping) or not isinstance(self.copiable_values,Mapping)
                or not _text(self.controller)):
            raise ValueError('A composition snapshot requires its closed version, object data and current controller')
        state=FrozenMap(self.card_state);values=FrozenMap(self.copiable_values)
        if 'permanent_composition' in state:
            raise ValueError('A physical snapshot cannot recursively contain a live composition')
        try:card=CardInstance.from_dict(thaw_value(state))
        except (ValueError,TypeError,KeyError) as exc:raise ValueError('Composition card data is malformed') from exc
        identity=ContinuousObjectIdentity(card.object_id,card.logical_object_id)
        _validate_identity(identity)
        if (type(card.zone_change_counter) is not int or card.zone_change_counter<0
                or any(not _text(value) for value in (card.ref,card.oracle_id,card.owner,card.controller))
                or any(type(value) is not bool for value in (card.is_token,card.is_commander,card.face_down,card.tapped))
                or FrozenMap(card.to_dict())!=state):
            raise ValueError('Composition card data must be a canonical physical-state snapshot')
        if card.zone=='battlefield':
            if self.controller!=card.controller or self.stack_ref is not None or self.stack_kind is not None:
                raise ValueError('A battlefield snapshot requires its current permanent controller and no stack context')
        elif card.zone=='stack':
            if not _text(self.stack_ref) or self.stack_kind not in ('spell','spell_copy'):
                raise ValueError('A stack snapshot requires its exact current spell context')
        else:raise ValueError('Composition capture is scoped to battlefield objects and stack spells')
        object.__setattr__(self,'card_state',state)
        object.__setattr__(self,'copiable_values',values)

    @classmethod
    def capture(cls,card,*,copiable_values,controller,stack_ref=None,stack_kind=None):
        from .model import CardInstance
        if not isinstance(card,CardInstance):raise ValueError('Physical capture requires an authoritative CardInstance')
        return cls(card.to_dict(),copiable_values,controller,stack_ref,stack_kind)

    @property
    def identity(self):
        return ContinuousObjectIdentity(self.card_state['object_id'],
            f"{self.card_state['object_id']}@{self.card_state['zone_change_counter']}")

    def detached_card(self):
        """Reconstruct captured data in a fresh object without live insertion."""
        from .model import CardInstance
        return CardInstance.from_dict(thaw_value(self.card_state))

    def to_dict(self):
        return {'schema_version':self.schema_version,'card_state':thaw_value(self.card_state),
            'copiable_values':thaw_value(self.copiable_values),'controller':self.controller,
            'stack_ref':self.stack_ref,'stack_kind':self.stack_kind}

    @classmethod
    def from_dict(cls,value):
        if (not isinstance(value,Mapping) or set(value)!={'schema_version','card_state','copiable_values',
                'controller','stack_ref','stack_kind'}):
            raise ValueError('Composition snapshots have a closed wire shape')
        return cls(value['card_state'],value['copiable_values'],value['controller'],value['stack_ref'],
            value['stack_kind'],value['schema_version'])
