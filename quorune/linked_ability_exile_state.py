"""Narrow CR607 exile-link owner; source departure is not a duration return."""
from __future__ import annotations

from .errors import GameRuleError
from .continuous_effect_model import ContinuousObjectIdentity
from .linked_ability_exile_model import (
    LINKED_ABILITY_EXILE_ANNOTATION, LinkedAbilityExile, linked_exile_objects,
)
from .linked_exile_return_model import ExiledObjectIdentity, LinkedExileReturnSpec, LinkedFadingScope
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


def card_has_current_exile_link(card, *, source_incarnation: str | None, ability_pair: str) -> bool:
    """Check this card's current exile incarnation against one source ability pair."""
    if not isinstance(source_incarnation, str) or not source_incarnation:
        return False
    return any(
        link.source.logical_object_id == source_incarnation and link.ability_pair == ability_pair
        and link.exiled.matches_exiled_card(card)
        for link in _stored_links(card)
    )


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
    return exile_batch_for_linked_ability(
        host, cards=(card,), source=source, ability_pair=ability_pair, reason=reason,
        replacement_selections=replacement_selections,
    )


def exile_graveyard_card_for_linked_ability(
    host, *, object_ref, source, ability_pair, actor, reason, replacement_selections=(),
):
    """Link the actual exile incarnation produced by the canonical graveyard move."""
    from .public_zone_moves import exile_public_graveyard_card_transition

    _require_linked_pair(source, ability_pair)
    result = exile_public_graveyard_card_transition(
        host, object_ref, actor=actor, reason=reason, replacement_selections=replacement_selections,
    )
    if result.actual_destination == "exile":
        record_linked_ability_exile(
            host, source=source, ability_pair=ability_pair,
            objects=(ExiledObjectIdentity(result.object_id, result.object_ref, result.logical_object_id),),
        )
        current = host.state.cards.get(source.object_id)
        if current is not None and current.zone == "battlefield" and current.logical_object_id == source.logical_object_id:
            from .entry_results import retain_linked_copy_forms

            retain_linked_copy_forms(current)
    return result.object_ref


def exile_batch_for_linked_ability(host, *, cards, source, ability_pair, reason, replacement_selections=()):
    """Move the selected batch once before retaining its actual exiled members."""
    _require_linked_pair(source, ability_pair)
    if not cards:
        return ()
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "exile") for card in cards),
        reason=reason, replacement_selections=replacement_selections,
    )
    actual = tuple(ExiledObjectIdentity(value.object_id, value.ref, value.logical_object_id)
                   for value in moved if value.zone == "exile" and not value.is_token and not value.is_spell_copy)
    record_linked_ability_exile(host, source=source, ability_pair=ability_pair, objects=actual)
    return tuple(value.ref for value in moved)


def return_linked_ability_exile(host, *, source, ability_pair, actor, reason,
                               replacement_selections=(), aura_target_refs=None, owner_order=(), tapped=False,
                               to_hand=False):
    """Resolve a linked return only when its actual returning ability resolves."""
    objects = current_linked_ability_exile(host, source=source, ability_pair=ability_pair)
    if owner_order:
        ranks = {seat: index for index, seat in enumerate(owner_order)}
        objects = tuple(sorted(objects, key=lambda value: ranks.get(host.state.cards[value.object_id].owner, len(ranks))))
    if to_hand:
        returned = ZoneTransitionOwner(host).move_cards_simultaneously(
            tuple((value.object_id, "hand") for value in objects if host.state.cards[value.object_id].owner in owner_order),
            reason=reason, replacement_selections=replacement_selections,
        )
        return [card.ref for card in returned]
    return return_linked_exiled_objects(host, {
        'op': 'return_linked_exiled_objects',
        'objects': [value.to_dict() for value in objects],
        'spec': LinkedExileReturnSpec(tapped=tapped).to_dict(),
        **({'_replacement_selections': replacement_selections} if replacement_selections else {}),
        **({'_aura_target_refs': aura_target_refs} if aura_target_refs else {}),
    }, actor=actor, operation='return_linked_exiled_objects', reason=reason)


def resolve_linked_ability_instruction(host, item, effect, *, actor, reason, spec=None):
    scope = spec.fading_scope if spec is not None else None
    activation = scope is not None and effect["phase"] == "exile"
    if item.kind != ("activated_ability" if activation else "triggered_ability") or item.controller != actor:
        raise GameRuleError("A linked ability instruction requires its matching resolving ability")
    try:
        source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    except ValueError as exc:
        raise GameRuleError("A linked ability instruction lost its original source") from exc
    pair = effect["binding_id"]
    selections = tuple(effect.get("_replacement_selections", ()))
    if effect["phase"] == "return":
        return return_linked_ability_exile(host, source=source, ability_pair=pair, actor=actor, reason=reason,
            replacement_selections=selections, aura_target_refs=effect.get("_aura_target_refs"),
            owner_order=host.apnap_order(), tapped=spec.tapped if spec is not None else False,
            to_hand=scope is LinkedFadingScope.OPPONENT_HAND)
    refs = effect["cards"]
    refs = (refs,) if isinstance(refs, str) else refs or ()
    if activation:
        if (item.context.get("currently_resolving") is not True
                or item.context.get("targets_revalidated") is not True or len(item.targets) != 1):
            raise GameRuleError("A linked Fading activation lost its revalidated target")
        if scope is LinkedFadingScope.OPPONENT_HAND:
            opponent, = item.targets
            if opponent == actor or opponent not in host.apnap_order() or len(refs) > 1:
                raise GameRuleError("A linked hand exile requires its chosen opponent's card")
            if not refs:
                return []
            card = host._resolve_object(opponent, refs[0], zones={"hand"}, owned_only=True)
            return list(exile_for_linked_ability(
                host, card=card, source=source, ability_pair=pair, reason=reason,
                replacement_selections=selections,
            ))
        if tuple(refs) != tuple(item.targets):
            raise GameRuleError("A linked Fading exile requires the revalidated permanent")
    if spec is not None and spec.batch_departure_scope is not None:
        from .rules.linked_batch_departure_return import select_linked_batch_departure_cards

        cards = select_linked_batch_departure_cards(
            host, item, source, spec.batch_departure_scope, refs, actor=actor,
        )
        return list(exile_batch_for_linked_ability(
            host, cards=cards, source=source, ability_pair=pair, reason=reason,
            replacement_selections=selections,
        ))
    if not refs:
        return []
    if len(refs) != 1 or type(refs[0]) is not str or not refs[0]:
        raise GameRuleError("A linked exile requires its one legal target")
    card = host._resolve_object(actor, refs[0], zones={"battlefield"})
    if card.phased_out:
        raise GameRuleError("A linked exile target is phased out")
    if activation and scope.value not in host._effective_type_parts(card)[0]:
        raise GameRuleError("A linked Fading exile lost its required target type")
    return list(exile_for_linked_ability(host, card=card, source=source, ability_pair=pair, reason=reason,
                                         replacement_selections=selections))


def retain_linked_graveyard_entry_source(card):
    """Keep the entry's native ability instance on this physical object."""
    from .rules.linked_graveyard_entry import LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD, linked_graveyard_entry_scope

    card.annotations[LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD] = linked_graveyard_entry_scope(card)


def expire_linked_graveyard_entry_source(card, expiry=None):
    from .rules.linked_graveyard_entry import LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD

    retained = card.annotations.get(LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD)
    if retained is not None and (expiry is None or retained["expiry"] == expiry.value):
        card.annotations.pop(LINKED_GRAVEYARD_ENTRY_SOURCE_FIELD)


def commit_linked_graveyard_entry(host, prepared, card):
    from .rules.linked_exile_copy import LINKED_EXILE_COPY_PAIR
    from .rules.linked_graveyard_entry import linked_graveyard_entry_actual_exiles

    identities = linked_graveyard_entry_actual_exiles(prepared.event, prepared.graveyard_copy_exiles)
    if identities and card.zone == "battlefield":
        record_linked_ability_exile(
            host, source=ContinuousObjectIdentity(card.object_id, card.logical_object_id),
            ability_pair=LINKED_EXILE_COPY_PAIR, objects=identities,
        )
        retain_linked_graveyard_entry_source(card)


def retain_linked_artifact_search_result(host, item, effect, card, *, actor):
    from .linked_library_imprint_model import uses_linked_artifact_library_instruction
    from .rules.linked_library_imprint import linked_artifact_library_source

    if not uses_linked_artifact_library_instruction(effect):
        return card
    try:
        source, pair = linked_artifact_library_source(host, item, effect, actor=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if card.zone == "exile" and not card.is_token and not card.is_spell_copy:
        record_linked_ability_exile(host, source=source, ability_pair=pair,
            objects=(ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id),))
    return card
