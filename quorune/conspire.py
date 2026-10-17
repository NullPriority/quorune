from __future__ import annotations

"""Pure Conspire trigger construction from the committed pair of tap costs."""

from collections.abc import Mapping
from copy import deepcopy
from .conspire_model import FixedConspireSpec, CONSPIRE_FIELD, CONSPIRE_SEMANTIC_KEY, validate_conspire_fact
from .errors import StateInvariantError
from .model import StackItem
from .rules.casting.conspire_costs import compiled_conspire_spec
from .storm import spell_copy_template


def conspire_trigger_items(host, *, spell, card, program):
    raw = spell.context.get(CONSPIRE_FIELD)
    if raw is None:
        return ()
    fact = validate_conspire_fact(raw)
    spec = compiled_conspire_spec(host, card)
    if spec is None or spec.instance_id != fact['instance_id']:
        raise StateInvariantError('Conspire trigger does not match its paid source instance')
    ref = host._next_ref('S')
    return (StackItem(stack_id=host._stable_runtime_id('stack', ref), ref=ref, kind='triggered_ability',
        controller=spell.controller, label=spell.label + ' — Conspire', source_object_id=card.object_id,
        semantic_key=CONSPIRE_SEMANTIC_KEY, visibility=list(host.seats),
        context={'copy_count': 1, 'copy_template': spell_copy_template(host, spell, program),
            CONSPIRE_FIELD: fact, 'conspire_spec': spec.to_dict()}),)


def validated_conspire_trigger(item):
    if item.semantic_key != CONSPIRE_SEMANTIC_KEY:
        raise StateInvariantError('Conspire owner received another trigger')
    try:
        fact = validate_conspire_fact(item.context.get(CONSPIRE_FIELD))
        spec = FixedConspireSpec.from_dict(item.context.get('conspire_spec'))
    except (ValueError, TypeError) as exc:
        raise StateInvariantError('Conspire trigger payment contract is malformed') from exc
    count = item.context.get('copy_count')
    template = item.context.get('copy_template')
    if (type(count) is not int or count != 1 or fact['instance_id'] != spec.instance_id
            or not isinstance(template, Mapping)):
        raise StateInvariantError('Conspire trigger copy specification changed')
    return 1, deepcopy(dict(template))
