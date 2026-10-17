from __future__ import annotations

"""Immutable resolution-time exclusions from current keyword prohibitions."""

from typing import Any, Sequence
from copy import copy
from dataclasses import replace
from .continuous_effect_model import ContinuousOperation


def denied_resolution_keyword_grants(host: Any, targets: Sequence[Any],
                                     operations: Sequence[ContinuousOperation]
                                     ) -> tuple[tuple[str, str, str], ...]:
    keywords = {str(op.value).casefold() for op in operations if op.op == 'add_ability'}
    if not keywords:
        return ()
    rows = []
    for card in targets:
        denied = set(host._effective_card_data(card).get('prohibited_keywords', ()))
        rows.extend((card.object_id, card.logical_object_id, keyword) for keyword in sorted(keywords & denied))
    return tuple(rows)


def prepare_resolution_keyword_exclusions(host: Any, targets: Sequence[Any], prototypes):
    """Evaluate lower layers and same-layer source removals before grant creation."""
    if not any(op.op == 'add_ability' for effect in prototypes for op in effect.operations):
        return tuple(prototypes)
    projected = copy(host)
    earlier = []
    for effect in prototypes:
        operations = tuple(op for op in effect.operations if effect.layer < 6
            or (effect.layer == 6 and op.op != 'add_ability'))
        if operations:
            earlier.append(replace(effect, operations=operations, denied_keyword_grants=()))
    projected.state = replace(host.state, continuous_effects=[*host.state.continuous_effects, *earlier])
    return tuple(replace(effect, denied_keyword_grants=denied_resolution_keyword_grants(
        projected, targets, effect.operations)) for effect in prototypes)
