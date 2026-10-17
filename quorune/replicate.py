from __future__ import annotations

"""Pure cast-trigger construction from the admitted Replicate payment fact."""

from collections.abc import Mapping
from copy import deepcopy
from .errors import StateInvariantError
from .model import StackItem
from .replicate_model import (
    FixedManaReplicateSpec, REPLICATE_FIELD, REPLICATE_SEMANTIC_KEY, validate_replicate_fact,
)
from .rules.casting.replicate_costs import compiled_replicate_spec
from .storm import spell_copy_template


def replicate_trigger_items(host, *, spell, card, program):
    paid = spell.context.get(REPLICATE_FIELD)
    if paid is None:
        return ()
    fact = validate_replicate_fact(paid)
    spec = compiled_replicate_spec(host, card)
    if spec is None or spec.payment_fact(fact['count']) != fact:
        raise StateInvariantError('Replicate trigger does not match its paid source instance')
    ref = host._next_ref('S')
    return (StackItem(stack_id=host._stable_runtime_id('stack', ref), ref=ref, kind='triggered_ability',
        controller=spell.controller, label=spell.label + ' — Replicate', source_object_id=card.object_id,
        semantic_key=REPLICATE_SEMANTIC_KEY, visibility=list(host.seats),
        context={'copy_count': fact['count'], 'copy_template': spell_copy_template(host, spell, program),
            REPLICATE_FIELD: fact, 'replicate_spec': spec.to_dict()}),)


def validated_replicate_trigger(item):
    if item.semantic_key != REPLICATE_SEMANTIC_KEY:
        raise StateInvariantError('Replicate owner received another trigger')
    try:
        fact = validate_replicate_fact(item.context.get(REPLICATE_FIELD))
        spec = FixedManaReplicateSpec.from_dict(item.context.get('replicate_spec'))
    except (ValueError, TypeError) as exc:
        raise StateInvariantError('Replicate trigger payment contract is malformed') from exc
    count = item.context.get('copy_count')
    template = item.context.get('copy_template')
    if (type(count) is not int or count != fact['count'] or spec.payment_fact(count) != fact
            or not isinstance(template, Mapping)):
        raise StateInvariantError('Replicate trigger copy specification changed')
    return count, deepcopy(dict(template))
