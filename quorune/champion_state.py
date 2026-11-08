"""Champion movement delegates to the existing zone and linked-exile owners."""
from __future__ import annotations

from .champion_model import champion_candidates
from .continuous_effect_model import Layer
from .errors import GameRuleError
from .linked_ability_exile_state import exile_for_linked_ability
from .object_query import object_query_result


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
    return exile_for_linked_ability(host, card=card, source=source, ability_pair=ability_pair, reason=reason,
                                    replacement_selections=replacement_selections)
