from __future__ import annotations

"""Resolution-boundary owner of immutable controller-scoped lifetime names."""

from .errors import StateInvariantError
from .spell_resolution_name_model import SpellResolutionNameLedger


def stack_spell_rules_names(card, effective):
    """Read canonical English name characteristics, including both fused names."""
    if card.face_down:
        return ()
    if card.zone != 'stack':
        raise StateInvariantError('Resolution names require the actual stack object')
    name = effective.get('name')
    if type(name) is not str:
        raise StateInvariantError('Named spell lost its effective name characteristic')
    # Multi-name spell characteristics use this canonical separator. This reads
    # the name characteristic, never Oracle text or the stack display label.
    names = tuple(sorted(set(name.split(' // ')))) if name else ()
    if any(not value or value != value.strip() for value in names):
        raise StateInvariantError('Malformed spell name characteristic')
    return names


def _commit_ledger(state, ledger):
    if not isinstance(ledger, SpellResolutionNameLedger):
        raise StateInvariantError('Resolution name history requires a typed ledger')
    state.spell_resolution_name_ledger = ledger


def begin_spell_resolution_names(host, item):
    ledger = host.state.spell_resolution_name_ledger
    if ledger is None or item.kind not in {'spell', 'spell_copy'}:
        return None
    card = host.state.cards.get(item.card_object_id)
    if card is None:
        raise StateInvariantError('A resolving spell requires its physical or copy object')
    names = stack_spell_rules_names(card, host._effective_card_data(card))
    updated, fact = ledger.begin(stack_ref=item.ref, controller=item.controller, names=names)
    if updated is not ledger:
        _commit_ledger(host.state, updated)
    return fact


def finish_spell_resolution_names(host, item):
    ledger = host.state.spell_resolution_name_ledger
    if ledger is None or item.kind not in {'spell', 'spell_copy'}:
        return
    updated = ledger.finish(item.ref)
    if updated is not ledger:
        _commit_ledger(host.state, updated)
