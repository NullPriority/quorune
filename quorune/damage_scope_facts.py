from __future__ import annotations

"""Sealed public creature-state facts consumed by prevention scopes."""

from typing import Mapping
from .damage_prevention_permission import UNPREVENTABLE_SOURCE_FACT

_FACTS=frozenset({'attacking','unblocked','token','enchanted','no-plus-one-counter',UNPREVENTABLE_SOURCE_FACT})


def canonical_damage_scope_facts(values):
    if not isinstance(values,(list,tuple)) or any(type(v) is not str or v not in _FACTS for v in values):
        raise ValueError('Damage scope facts require a closed public vocabulary')
    return tuple(sorted(set(values)))


def canonical_damage_partner_identities(values):
    if not isinstance(values,(list,tuple)) or any(type(v) is not str or not v for v in values):
        raise ValueError('Damage combat partners require nonempty logical identities')
    return tuple(sorted(set(values)))


def current_damage_scope_facts(host,card,data: Mapping):
    facts=[]
    if card.is_token:facts.append('token')
    if card.object_id in host.state.combat.attackers:
        facts.append('attacking')
        if host.state.combat.blockers_declared and card.object_id not in host.state.combat.blockers:facts.append('unblocked')
    if data.get('enchanted') is True:facts.append('enchanted')
    if card.counters.get('+1/+1',0)==0:facts.append('no-plus-one-counter')
    return tuple(sorted(facts))


def current_damage_combat_partners(host,card):
    combat=host.state.combat
    if card.zone!='battlefield' or card.phased_out:return ()
    opponents=set(combat.blockers.get(card.object_id,()))
    opponents.update(attacker for attacker,blockers in combat.blockers.items() if card.object_id in blockers and attacker in combat.attackers)
    return tuple(sorted(host.state.cards[oid].logical_object_id for oid in opponents if oid in host.state.cards and host.state.cards[oid].zone=='battlefield' and not host.state.cards[oid].phased_out))
