from __future__ import annotations

"""Compose existing public type queries with source-conditioned entry state."""

import re

from ..qualified_entry_state_model import (
    QUALIFIED_ENTRY_STATE_CAPABILITY, QUALIFIED_ENTRY_STATE_HANDLER, QualifiedEntryStateSpec,
)
from ..rules.source_references import SourceReferenceSpec
from .public_state_queries import fixed_battlefield_query_subject, _controller_relation_suffix


def _entry_subject(subject):
    parsed = fixed_battlefield_query_subject(subject)
    if parsed is not None:
        return parsed[0], (parsed[1],), parsed[2]
    shared = _controller_relation_suffix(subject, 'any')
    if shared is None:
        return None
    material, relation = shared
    exclude = material.casefold().startswith('other ')
    material = material[6:] if exclude else material
    parts = material.split(' and ')
    if not 2 <= len(parts) <= 4:
        return None
    leaves = [fixed_battlefield_query_subject(part) for part in parts]
    if any(leaf is None or leaf[0] != 'any' or leaf[2] for leaf in leaves):
        return None
    return relation, tuple(leaf[1] for leaf in leaves), exclude


def qualified_entry_state_handler(text, *, source_name):
    material = ' '.join(text.strip().split())
    condition = re.fullmatch(r'As long as (?P<source>.+?) is (?P<state>tapped|untapped), (?P<body>.+)', material, re.I)
    source_tapped = None
    if condition is not None:
        if not source_name or not SourceReferenceSpec(source_name).matches(condition['source']):
            return None
        source_tapped = condition['state'].casefold() == 'tapped'
        material = condition['body']
    match = re.fullmatch(r'(?P<subject>.+?) enter (?P<state>tapped|untapped)\.?', material, re.I)
    if match is None or (condition is None and match['state'].casefold() == 'untapped'):
        return None
    parsed = _entry_subject(match['subject'])
    if parsed is None:
        return None
    relation, queries, exclude = parsed
    try:
        spec = QualifiedEntryStateSpec({'any': 'all', 'source_controller': 'controller',
            'source_opponents': 'opponents'}[relation], queries, exclude, source_tapped,
            match['state'].casefold() == 'tapped')
    except (KeyError, ValueError):
        return None
    return ('external-entry-state-qualified-v2', {'handler_id': QUALIFIED_ENTRY_STATE_HANDLER,
            'schema_version': 2, 'event': 'zone.change', **spec.to_dict()},
            QUALIFIED_ENTRY_STATE_CAPABILITY)
