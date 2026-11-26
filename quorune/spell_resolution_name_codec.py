from __future__ import annotations

"""Additive spell-name history checkpoint and record provenance codecs."""

from .spell_resolution_name_model import SpellResolutionNameFact, SpellResolutionNameLedger


def resolution_name_state_fields(ledger):
    return {'spell_resolution_name_ledger': ledger.to_dict()} if ledger is not None else {}


def read_resolution_name_ledger(data):
    if 'spell_resolution_name_ledger' not in data:
        return None
    ledger = SpellResolutionNameLedger.from_dict(data['spell_resolution_name_ledger'])
    if ledger.cast_history is not None and not set(ledger.cast_history.counts_by_caster) <= set(data["players"]):
        raise ValueError("Spell cast history names a caster outside the game")
    stack = {item['ref']: item for item in data.get('stack', ())}
    for ref, raw in ledger.pending.items():
        fact = SpellResolutionNameFact.from_dict(raw)
        item = stack.get(ref)
        if item is None or item.get('kind') not in {'spell', 'spell_copy'} or item.get('controller') != fact.controller:
            raise ValueError('Pending resolution history lost its original active spell')
    return ledger


def validate_resolution_name_provenance(format_value, ledger):
    field = 'spell_resolution_name_history_version'
    declared = format_value.get(field)
    actual = 1 if ledger is not None else None
    if ((field in format_value and (type(declared) is not int or declared != 1)) or declared != actual):
        raise ValueError('Spell resolution name history provenance does not match the initial checkpoint')


def spell_name_provenance_fields(state):
    fields = {}
    ledger = getattr(state, "spell_resolution_name_ledger", None)
    if ledger is not None:
        fields["spell_resolution_name_history_version"] = 1
        if ledger.cast_history is not None:
            fields["spell_cast_name_history_version"] = 1
    return fields


def validate_spell_name_provenance(format_value, state):
    validate_resolution_name_provenance(format_value, getattr(state, "spell_resolution_name_ledger", None))
    field = "spell_cast_name_history_version"
    expected = spell_name_provenance_fields(state).get(field)
    declared = format_value.get(field)
    if declared != expected or field in format_value and type(declared) is not int:
        raise ValueError("Spell cast name history provenance does not match the initial checkpoint")



def has_required_spell_name_history(state, dependencies):
    from .spell_cast_name_model import SPELL_CAST_NAME_HISTORY_CAPABILITY

    ledger = state.spell_resolution_name_ledger
    return (("history.spell_resolution.names" not in dependencies or ledger is not None)
            and (SPELL_CAST_NAME_HISTORY_CAPABILITY not in dependencies
                 or ledger is not None and ledger.cast_history is not None))
