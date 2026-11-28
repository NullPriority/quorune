from __future__ import annotations

"""Record actual cast names and the hand owner before stack interaction."""

from dataclasses import replace

from .errors import StateInvariantError
from .spell_cast_name_model import CAST_NAME_RECEIPT_FIELD, SPELL_CAST_NAME_HISTORY_CAPABILITY, SpellCastNameReceipt
from .spell_resolution_names import _commit_ledger, stack_spell_rules_names


def record_spell_cast_names(host, *, item, card, effective, caster, origin, program):
    ledger = getattr(host.state, "spell_resolution_name_ledger", None)
    if ledger is None or ledger.cast_history is None:
        return
    if item.kind != "spell" or item.controller != caster or item.card_object_id != card.object_id:
        raise StateInvariantError("Only an actual committed cast provides spell cast names")
    names = stack_spell_rules_names(card, effective)
    if names:
        history = ledger.cast_history.note_cast(caster=caster, names=names)
        _commit_ledger(host.state, replace(ledger, cast_history=history))
        if program is not None and SPELL_CAST_NAME_HISTORY_CAPABILITY in program.capability_dependencies:
            receipt = SpellCastNameReceipt(item.ref, caster, card.owner if origin == "hand" else None, names)
            item.context[CAST_NAME_RECEIPT_FIELD] = receipt.to_dict()
