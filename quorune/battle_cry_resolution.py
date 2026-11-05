"""Battle Cry's live resolution set and retained historical declared-set boundary."""
from .attack_transition_model import AttackObjectIdentity, AttackTransitionError
from .battle_cry_resolution_model import battle_cry_resolution_version


def _current_battle_cry_identities(state, occurrence):
    identities = []
    for object_id in state.combat.attackers:
        card = state.cards.get(object_id)
        if card is None or card.zone != 'battlefield':
            raise AttackTransitionError('Battle Cry attacking set contains an unavailable permanent')
        if (object_id == occurrence.source.object_id
                and card.logical_object_id == occurrence.source.logical_object_id):
            continue
        identities.append(AttackObjectIdentity(object_id, card.logical_object_id, card.ref))
    return tuple(sorted(identities, key=lambda value: (value.reference, value.object_id)))


def battle_cry_recipient_identities(state, occurrence):
    try:
        version = battle_cry_resolution_version(getattr(state, 'battle_cry_resolution_version', None))
    except ValueError as exc:
        raise AttackTransitionError(str(exc)) from exc
    if version == 1:
        return _current_battle_cry_identities(state, occurrence)
    expected = {occurrence.source.object_id: occurrence.source,
        **{value.object_id: value for value in occurrence.affected}}
    if set(state.combat.attackers) - set(expected):
        raise AttackTransitionError('Battle Cry cannot resolve across an unrepresented new attacker')
    for object_id in set(state.combat.attackers) & set(expected):
        card = state.cards.get(object_id)
        if card is not None and card.logical_object_id != expected[object_id].logical_object_id:
            raise AttackTransitionError('Battle Cry cannot resolve across a changed attacker identity')
    return occurrence.affected
