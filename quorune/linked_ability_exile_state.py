"""Narrow CR607 exile-link owner; source departure is not a duration return."""
from __future__ import annotations

from .errors import GameRuleError
from .continuous_effect_model import ContinuousObjectIdentity
from .linked_ability_exile_model import (
    LINKED_ABILITY_EXILE_ANNOTATION, LinkedAbilityExile, linked_exile_objects,
)
from .linked_exile_return_model import ExiledObjectIdentity, LinkedExileReturnSpec
from .linked_exile_return import return_linked_exiled_objects


def _stored_links(card):
    raw = card.annotations.get(LINKED_ABILITY_EXILE_ANNOTATION, [])
    if not isinstance(raw, list):
        raise GameRuleError('Linked ability exile payload must be a closed list')
    try:
        links = tuple(LinkedAbilityExile.from_dict(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if any(link.exiled.object_id != card.object_id or link.exiled.ref != card.ref for link in links):
        raise GameRuleError('An exile-link payload belongs to a different physical object')
    return links


def record_linked_ability_exile(host, *, source, ability_pair, objects):
    """Record only actual exile incarnations after canonical movement commits."""
    if (not isinstance(source, ContinuousObjectIdentity) or type(ability_pair) is not str
            or not ability_pair.strip() or ability_pair != ability_pair.strip()):
        raise GameRuleError('Exile links require an exact original source and ability pair')
    pending = []
    for identity in tuple(objects):
        if not isinstance(identity, ExiledObjectIdentity):
            raise GameRuleError('An exile link requires its actual typed incarnation')
        card = host.state.cards.get(identity.object_id)
        if not identity.matches_exiled_card(card):
            raise GameRuleError('An exile link cannot bind an absent or changed object')
        try:
            link = LinkedAbilityExile(source, ability_pair, identity)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        retained = _stored_links(card)
        if link not in retained:
            pending.append((card, (*retained, link)))
    if len({card.object_id for card, _links in pending}) != len(pending):
        raise GameRuleError('An exile-link batch cannot repeat an object')
    # A malformed later member cannot leave an earlier link partially written.
    for card, links in pending:
        card.annotations[LINKED_ABILITY_EXILE_ANNOTATION] = [link.to_dict() for link in links]
    return tuple(link.exiled for _card, links in pending for link in links[-1:])


def current_linked_ability_exile(host, *, source, ability_pair):
    """Find this source pair without interpreting or depending on a live source."""
    links, current = [], []
    for card in host.state.cards.values():
        if card.zone != 'exile' or card.is_token or card.is_spell_copy:
            continue
        links.extend(_stored_links(card))
        current.append(ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id))
    return linked_exile_objects(links, source=source, ability_pair=ability_pair, current_exiled=current)


def return_linked_ability_exile(host, *, source, ability_pair, actor, reason, replacement_selections=()):
    """Resolve a linked return only when its actual returning ability resolves."""
    objects = current_linked_ability_exile(host, source=source, ability_pair=ability_pair)
    return return_linked_exiled_objects(host, {
        'op': 'return_linked_exiled_objects',
        'objects': [value.to_dict() for value in objects],
        'spec': LinkedExileReturnSpec().to_dict(),
        **({'_replacement_selections': replacement_selections} if replacement_selections else {}),
    }, actor=actor, operation='return_linked_exiled_objects', reason=reason)
