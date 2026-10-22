from __future__ import annotations

from collections.abc import Mapping
from ..banding_model import AttackBand, band_member_ids, normalize_attack_bands
from ..errors import GameRuleError


def banding_offer(host, candidates):
    eligible = [row['id'] for row in candidates if 'banding' in host._combat_keywords(
        host._resolve_object(host.state.active_player, row['id'], zones={'battlefield'}))]
    return {'banding_attackers': eligible, 'maximum_without_banding': 1, 'same_defender': True,
        'rows': [{'value': row['id'], 'options': [{'value': ref, 'label': ref} for ref in eligible]}
            for row in candidates]} if eligible else None


def prepare_attack_bands(host, chosen, raw):
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        groups = {}
        for member, leader in raw.items():
            if type(member) is not str or type(leader) is not str:
                raise GameRuleError('Band assignments require attacker references')
            groups.setdefault(leader, []).append(member)
        by_ref = {card.ref: card for card, _details in chosen}
        if any(leader not in members or leader not in by_ref
                or 'banding' not in host._combat_keywords(by_ref[leader]) for leader, members in groups.items()):
            raise GameRuleError('Each band leader must be a member with banding')
        raw = list(groups.values())
    if not isinstance(raw, (list, tuple)):
        raise GameRuleError('Attack bands must be arrays of attacker references')
    by_ref = {card.ref: (card, details) for card, details in chosen}
    used = set()
    result = []
    for group in raw:
        if not isinstance(group, (list, tuple)) or not group or any(type(ref) is not str for ref in group):
            raise GameRuleError('Each attack band requires current attacker references')
        if len(set(group)) != len(group) or used.intersection(group) or any(ref not in by_ref for ref in group):
            raise GameRuleError('Band members must be distinct declared attackers in only one band')
        cards = [by_ref[ref][0] for ref in group]
        banding = ['banding' in host._combat_keywords(card) for card in cards]
        if not any(banding) or banding.count(False) > 1:
            raise GameRuleError('Ordinary attack bands require banding and at most one member without banding')
        if len({by_ref[ref][1]['target'] for ref in group}) != 1:
            raise GameRuleError('All members of an attack band must attack the same defender')
        result.append(AttackBand(tuple((card.object_id, card.logical_object_id) for card in cards)))
        used.update(group)
    return normalize_attack_bands(tuple(result))


def expanded_block_pairs(host, chosen):
    if not host.state.combat.attack_bands:
        return tuple(chosen)
    result = {}
    for blocker, attacker in chosen:
        for oid in band_member_ids(host.state.combat, host.state.cards, attacker.object_id):
            result[(blocker.object_id, oid)] = (blocker, host.state.cards[oid])
    return tuple(sorted(result.values(), key=lambda pair: (pair[1].ref, pair[0].ref,
        pair[1].object_id, pair[0].object_id)))
