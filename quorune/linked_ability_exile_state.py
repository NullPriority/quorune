"""Narrow CR607 exile-link owner; source departure is not a duration return."""
from __future__ import annotations

from .errors import GameRuleError
from .continuous_effect_model import ContinuousObjectIdentity
from .linked_ability_exile_model import (
    LINKED_ABILITY_EXILE_ANNOTATION, LinkedAbilityExile, linked_exile_objects,
)
from .linked_exile_return_model import ExiledObjectIdentity, LinkedExileReturnSpec
from .linked_exile_return import return_linked_exiled_objects
from .zone_transitions import ZoneTransitionOwner


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


def _require_linked_pair(source, ability_pair):
    if (not isinstance(source, ContinuousObjectIdentity) or type(ability_pair) is not str
            or not ability_pair.strip() or ability_pair != ability_pair.strip()):
        raise GameRuleError('Exile links require an exact original source and ability pair')


def record_linked_ability_exile(host, *, source, ability_pair, objects):
    """Record only actual exile incarnations after canonical movement commits."""
    _require_linked_pair(source, ability_pair)
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


def exile_for_linked_ability(host, *, card, source, ability_pair, reason, replacement_selections=()):
    """Commit one admitted subject and retain only its actual exile incarnation."""
    _require_linked_pair(source, ability_pair)
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        ((card.object_id, "exile"),), reason=reason, replacement_selections=replacement_selections,
    )
    actual = tuple(ExiledObjectIdentity(value.object_id, value.ref, value.logical_object_id)
                   for value in moved if value.zone == "exile" and not value.is_token and not value.is_spell_copy)
    record_linked_ability_exile(host, source=source, ability_pair=ability_pair, objects=actual)
    return tuple(value.ref for value in moved)


def return_linked_ability_exile(host, *, source, ability_pair, actor, reason,
                               replacement_selections=(), aura_target_refs=None, owner_order=()):
    """Resolve a linked return only when its actual returning ability resolves."""
    objects = current_linked_ability_exile(host, source=source, ability_pair=ability_pair)
    if owner_order:
        ranks = {seat: index for index, seat in enumerate(owner_order)}
        objects = tuple(sorted(objects, key=lambda value: ranks.get(host.state.cards[value.object_id].owner, len(ranks))))
    return return_linked_exiled_objects(host, {
        'op': 'return_linked_exiled_objects',
        'objects': [value.to_dict() for value in objects],
        'spec': LinkedExileReturnSpec().to_dict(),
        **({'_replacement_selections': replacement_selections} if replacement_selections else {}),
        **({'_aura_target_refs': aura_target_refs} if aura_target_refs else {}),
    }, actor=actor, operation='return_linked_exiled_objects', reason=reason)


def resolve_linked_ability_instruction(host, item, effect, *, actor, reason):
    if item.kind != "triggered_ability" or item.controller != actor:
        raise GameRuleError("A linked ability instruction requires its resolving trigger")
    try:
        source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    except ValueError as exc:
        raise GameRuleError("A linked ability instruction lost its original source") from exc
    pair = effect["binding_id"]
    selections = tuple(effect.get("_replacement_selections", ()))
    if effect["phase"] == "return":
        return return_linked_ability_exile(host, source=source, ability_pair=pair, actor=actor, reason=reason,
            replacement_selections=selections, aura_target_refs=effect.get("_aura_target_refs"), owner_order=host.apnap_order())
    refs = effect["cards"]
    refs = (refs,) if isinstance(refs, str) else refs
    if not refs:
        return []
    if len(refs) != 1 or type(refs[0]) is not str or not refs[0]:
        raise GameRuleError("A linked exile requires its one legal target")
    card = host._resolve_object(actor, refs[0], zones={"battlefield"})
    if card.phased_out:
        raise GameRuleError("A linked exile target is phased out")
    return list(exile_for_linked_ability(host, card=card, source=source, ability_pair=pair, reason=reason,
                                         replacement_selections=selections))
