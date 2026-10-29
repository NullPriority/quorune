"""Champion movement delegates to the existing zone and linked-exile owners."""
from __future__ import annotations

from .champion_model import champion_candidates
from .continuous_effect_model import Layer
from .errors import GameRuleError
from .linked_ability_exile_state import record_linked_ability_exile
from .linked_exile_return_model import ExiledObjectIdentity
from .object_query import object_query_result
from .zone_transitions import ZoneTransitionOwner


def sacrifice_champion_source_if_controlled(host, *, source, actor, reason, replacement_selections=()):
    card = host.state.cards.get(source.object_id)
    if (card is None or card.zone != 'battlefield' or card.phased_out
            or card.logical_object_id != source.logical_object_id or card.controller != actor):
        return False
    host.apply_effect({'op': 'sacrifice', 'card': card.ref, 'reason': reason,
        **({'_replacement_selections': replacement_selections} if replacement_selections else {})}, actor=actor)
    return True


def exile_champion_permanent(host, *, source, ability_pair, actor, subject, subject_ref, spec, reason,
                            replacement_selections=()):
    if type(ability_pair) is not str or not ability_pair or ability_pair != ability_pair.strip():
        raise GameRuleError('Champion requires an exact linked ability pair before movement')
    card = host.state.cards.get(subject.object_id)
    if card is None or card.ref != subject_ref or card.logical_object_id != subject.logical_object_id:
        raise GameRuleError('The chosen Champion object changed incarnation')
    effective = host._effective_card_data(card, maximum_layer=Layer.TYPE)
    row = object_query_result(card, effective, type_parts=host._type_parts(str(effective.get('type_line') or '')),
                              known_to_actor=True, attached_to_ref=None)
    if not champion_candidates((row,), spec=spec, actor=actor, source=source):
        raise GameRuleError('The chosen Champion object is no longer eligible')
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(((card.object_id, 'exile'),), reason=reason,
        replacement_selections=replacement_selections)
    actual = tuple(ExiledObjectIdentity(value.object_id, value.ref, value.logical_object_id)
                   for value in moved if value.zone == 'exile' and not value.is_token and not value.is_spell_copy)
    record_linked_ability_exile(host, source=source, ability_pair=ability_pair, objects=actual)
    return tuple(value.ref for value in moved)
