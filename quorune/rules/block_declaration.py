from __future__ import annotations

"""Shared current-object validation for ordinary and multiple block declarations."""

from collections.abc import Mapping
from .block_capacity import current_block_capacity
from ..errors import GameRuleError


def committed_block_mapping(host, assignments):
    grouped={}
    for assignment in assignments:
        blocker=host.state.cards[assignment.blocker_object_id].ref
        attacker=host.state.cards[assignment.attacker_object_id].ref
        grouped.setdefault(blocker,[]).append(attacker)
    return {blocker:refs[0] if len(refs)==1 else sorted(refs) for blocker,refs in grouped.items()}


def validate_block_assignments(host, defender, raw):
    if raw is None:raw={}
    if not isinstance(raw,Mapping):raise GameRuleError('Block declaration must map blocker references to attacker references')
    chosen=[];canonical={};seen=set()
    for blocker_value,attacker_values in raw.items():
        if type(blocker_value) is not str:raise GameRuleError('Blocker reference must be a string')
        blocker=host._resolve_object(defender,blocker_value,zones={'battlefield'},controlled_only=True)
        if blocker.object_id in seen:raise GameRuleError('A blocker may have only one assignment group')
        seen.add(blocker.object_id)
        values=tuple(attacker_values) if isinstance(attacker_values,(list,tuple)) else (attacker_values,)
        if not values or any(type(v) is not str or not v for v in values):raise GameRuleError('Block assignments require nonempty attacker references')
        types,_,_=host._type_parts(str(host._effective_card_data(blocker).get('type_line') or ''))
        if blocker.tapped or blocker.phased_out or 'creature' not in types or 'battle' in types:
            raise GameRuleError(f'{blocker.ref} cannot block')
        capacity=current_block_capacity(host,blocker,eligible_attackers=len(host._current_attacker_cards()))
        if len(values)>capacity:raise GameRuleError(f'{blocker.ref} can block at most {capacity} attackers')
        attacker_ids=set();references=[]
        for value in values:
            attacker=host._resolve_object(defender,value,zones={'battlefield'})
            if attacker.object_id in attacker_ids:raise GameRuleError('A blocker-attacker pair may appear only once')
            attacker_ids.add(attacker.object_id)
            target=host.state.combat.attackers.get(attacker.object_id)
            if target is None or host._defending_player_for_attacker(attacker.object_id,target)!=defender:
                raise GameRuleError(f'{attacker.ref} is not attacking {defender}')
            legal,reason=host._can_block(attacker,blocker)
            if not legal:raise GameRuleError(f'{blocker.ref} cannot block {attacker.ref}: {reason}')
            chosen.append((blocker,attacker));references.append(attacker.ref)
        canonical[blocker.ref]=references[0] if len(references)==1 else tuple(sorted(references))
    return chosen,canonical
