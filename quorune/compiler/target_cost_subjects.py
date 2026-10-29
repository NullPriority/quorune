from __future__ import annotations

"""Shared fixed public target subjects for spell and activation cost clauses."""

import re

from ..target_cost_subject import TargetCostSubjectSpec
from ..rules.source_references import SourceReferenceSpec
from .public_state_queries import fixed_battlefield_query_subject


def target_cost_subject(subject, *, source_name):
    normalized = ' '.join(subject.strip().split())
    if normalized.casefold() in {'this creature', 'this permanent', 'this artifact'} or (
            source_name and SourceReferenceSpec(source_name).matches(normalized)):
        return TargetCostSubjectSpec('source')
    if normalized.casefold() in {'enchanted creature', 'enchanted permanent'}:
        return TargetCostSubjectSpec('attached')
    query_text = re.sub(r'^(?:a|an) ', '', normalized, flags=re.I)
    query_text = re.sub(r'\b(creature|permanent|artifact|enchantment|land|planeswalker)(?= you control$|$)',
                        lambda match: match[0] + 's', query_text, flags=re.I)
    parsed = fixed_battlefield_query_subject(query_text)
    if parsed is None or parsed[2] or parsed[0] not in {'any', 'source_controller'}:
        return None
    try:
        return TargetCostSubjectSpec('query', parsed[1], parsed[0])
    except ValueError:
        return None
