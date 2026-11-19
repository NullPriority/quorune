from __future__ import annotations

"""Additive lifetime-resolution ledger checkpoint and record provenance codec."""

from .spell_resolution_name_model import SpellResolutionNameFact, SpellResolutionNameLedger


def resolution_name_state_fields(ledger):
    return {'spell_resolution_name_ledger': ledger.to_dict()} if ledger is not None else {}


def read_resolution_name_ledger(data):
    if 'spell_resolution_name_ledger' not in data:
        return None
    ledger = SpellResolutionNameLedger.from_dict(data['spell_resolution_name_ledger'])
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
