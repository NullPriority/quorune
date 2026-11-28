from __future__ import annotations

"""Typed noncopiable entry characteristics committed by the effect journal."""

from dataclasses import dataclass, replace
from typing import Mapping
from ..creature_subtypes import canonical_creature_subtype
from ..continuous_effect_model import (ContinuousEffect,ContinuousEffectDuration,ContinuousEffectOrigin,
    ContinuousObjectIdentity,ContinuousOperation,Layer)
from ..continuous_effect_state import commit_continuous_effect
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap


_RESOLUTION_ENTRY_GRANT = "resolution_entry_subtype_grant"
_RESOLUTION_ENTRY_ENCHANTMENT = "resolution_entry_enchantment_form"


@dataclass(frozen=True,slots=True,order=True)
class EntrySubtypeGrant:
    effect_id: str
    subtype: str
    sequence: int=0
    origin: ContinuousEffectOrigin = ContinuousEffectOrigin.REPLACEMENT

    def __post_init__(self):
        if type(self.effect_id) is not str or not self.effect_id or type(self.subtype) is not str or canonical_creature_subtype(self.subtype)!=self.subtype:
            raise ValueError('Entry subtype grant requires effect identity and canonical creature subtype')
        if type(self.sequence) is not int or self.sequence<0:raise ValueError('Entry subtype sequence must be nonnegative')
        if self.origin is not ContinuousEffectOrigin.REPLACEMENT and self.origin is not ContinuousEffectOrigin.RESOLUTION:
            raise ValueError("Entry subtype grants require a replacement or resolution origin")


@dataclass(frozen=True, slots=True)
class EntryEnchantmentForm:
    source_ref: str

    def __post_init__(self):
        if type(self.source_ref) is not str or not self.source_ref or self.source_ref.startswith("$"):
            raise ValueError("An enchantment entry needs its original resolving source")


def with_resolution_entry_enchantment(plan, form: EntryEnchantmentForm):
    """Attach the resolving type change to the final accepted entry."""
    if not isinstance(form, EntryEnchantmentForm):
        raise GameRuleError("A resolving enchantment entry needs a typed form")
    if plan.destination != "battlefield":
        return plan
    event = plan.prepared_replacement.event
    if _RESOLUTION_ENTRY_ENCHANTMENT in event.payload:
        raise GameRuleError("The entry already has a resolving enchantment form")
    payload = FrozenMap({**event.payload, _RESOLUTION_ENTRY_ENCHANTMENT: form.source_ref})
    prepared = replace(plan.prepared_replacement, event=replace(event, payload=payload))
    return replace(plan, prepared_replacement=prepared)


def entry_enchantment_form(event):
    if _RESOLUTION_ENTRY_ENCHANTMENT not in event.payload:
        return None
    return EntryEnchantmentForm(event.payload[_RESOLUTION_ENTRY_ENCHANTMENT])


def commit_entry_enchantment_form(host, card, form):
    if form is None:
        return
    from ..resolution_characteristic_model import IndefiniteEnchantmentFormSpec

    if not isinstance(form, EntryEnchantmentForm) or card.zone != "battlefield":
        raise ValueError("Enchantment form requires its resulting battlefield incarnation")
    (layer, sublayer, operations), = IndefiniteEnchantmentFormSpec().layer_operations()
    effect = ContinuousEffect(
        effect_id=f"entry-enchantment:{form.source_ref}:{card.logical_object_id}",
        source_id=form.source_ref, layer=layer, sublayer=sublayer, timestamp=host._next_zone_timestamp(),
        operations=operations, origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.ZONE_OBJECT,
        applies=ObjectQuerySpec(zones=("battlefield",)),
        locked_objects=(ContinuousObjectIdentity(card.object_id, card.logical_object_id),),
    )
    commit_continuous_effect(host.state, effect)


def with_resolution_entry_subtype(plan, grant: EntrySubtypeGrant):
    """Prepare a simultaneous CR 611.2e grant after replacement selection."""
    if not isinstance(grant, EntrySubtypeGrant) or grant.origin is not ContinuousEffectOrigin.RESOLUTION:
        raise GameRuleError("A resolving entry subtype requires a typed resolution grant")
    if plan.destination != "battlefield":
        return plan
    event = plan.prepared_replacement.event
    if _RESOLUTION_ENTRY_GRANT in event.payload:
        raise GameRuleError("The entry already has a resolving subtype grant")
    payload = FrozenMap({
        **event.payload,
        _RESOLUTION_ENTRY_GRANT: {
            "effect_id": grant.effect_id, "subtype": grant.subtype, "sequence": grant.sequence,
        },
    })
    prepared = replace(plan.prepared_replacement, event=replace(event, payload=payload))
    return replace(plan, prepared_replacement=prepared)


def _decode_entry_subtype(value, *, origin):
    if not isinstance(value, Mapping) or set(value) != {"effect_id", "subtype", "sequence"}:
        raise ValueError("Entry subtype grants use closed typed fields")
    return EntrySubtypeGrant(**value, origin=origin)


def entry_subtype_grants(event):
    values=event.payload.get('entry_subtype_grants',())
    if not isinstance(values,(list,tuple)):raise ValueError('Entry subtype grants must be an array')
    result = tuple(_decode_entry_subtype(value, origin=ContinuousEffectOrigin.REPLACEMENT) for value in values)
    if _RESOLUTION_ENTRY_GRANT in event.payload:
        return (*result, _decode_entry_subtype(event.payload[_RESOLUTION_ENTRY_GRANT],
            origin=ContinuousEffectOrigin.RESOLUTION))
    return result


def commit_entry_subtype_grants(host,card,grants):
    if card.zone!='battlefield':
        if grants:raise ValueError('Entry subtype grants require the resulting battlefield object')
        return ()
    effects=[]
    for grant in grants:
        if not isinstance(grant,EntrySubtypeGrant):raise ValueError('Entry subtype commit requires typed results')
        effect=ContinuousEffect(effect_id=f'entry-subtype:{grant.effect_id}:{card.logical_object_id}:{grant.sequence}',
            source_id=grant.effect_id,layer=Layer.TYPE,sublayer='4',timestamp=host._next_zone_timestamp(),
            operations=(ContinuousOperation('add_types',(grant.subtype.title(),),field='subtypes'),),
            origin=grant.origin,duration=ContinuousEffectDuration.ZONE_OBJECT,
            applies=ObjectQuerySpec(zones=('battlefield',), types_any=(
                ('creature', 'kindred') if grant.origin is ContinuousEffectOrigin.RESOLUTION else ()
            )),
            locked_objects=(ContinuousObjectIdentity(card.object_id,card.logical_object_id),))
        commit_continuous_effect(host.state,effect);effects.append(effect)
    return tuple(effects)
