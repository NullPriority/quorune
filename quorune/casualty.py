"""Ordinary one-copy cast triggers constructed from committed Casualty facts."""
from collections.abc import Mapping
from copy import deepcopy
from .casualty_model import FixedCasualtySpec, CASUALTY_FIELD, CASUALTY_SEMANTIC_KEY, validate_casualty_fact
from .errors import StateInvariantError
from .model import StackItem
from .rules.casting.casualty_costs import compiled_casualty_spec
from .storm import spell_copy_template


def casualty_trigger_items(host, *, spell, card, program):
    raw = spell.context.get(CASUALTY_FIELD)
    if raw is None:return ()
    fact = validate_casualty_fact(raw);spec = compiled_casualty_spec(host, card)
    if spec is None or spec.to_dict() != {k: fact[k] for k in ('schema_version', 'instance_id', 'minimum_power')}:
        raise StateInvariantError('Casualty trigger differs from its paid source instance')
    identity = fact['object']
    if not any(row.get('card_ref') == identity['ref'] and row.get('logical_object_id') == identity['logical_object_id']
            for row in spell.context.get('fixed_cast_lifecycle_cost_objects', ())):
        raise StateInvariantError('Casualty sacrifice differs from its committed cost incarnation')
    ref = host._next_ref('S')
    return (StackItem(stack_id=host._stable_runtime_id('stack', ref), ref=ref, kind='triggered_ability',
        controller=spell.controller, label=spell.label + ' — Casualty', source_object_id=card.object_id,
        semantic_key=CASUALTY_SEMANTIC_KEY, visibility=list(host.seats), context={'copy_count': 1,
            'copy_template': spell_copy_template(host, spell, program), CASUALTY_FIELD: fact, 'casualty_spec': spec.to_dict()}),)


def validated_casualty_trigger(item):
    if item.semantic_key != CASUALTY_SEMANTIC_KEY:raise StateInvariantError('Casualty owner received another trigger')
    try:
        fact = validate_casualty_fact(item.context.get(CASUALTY_FIELD))
        spec = FixedCasualtySpec.from_dict(item.context.get('casualty_spec'))
    except (ValueError, TypeError) as exc:raise StateInvariantError('Casualty trigger lost its paid declaration') from exc
    count = item.context.get('copy_count');template = item.context.get('copy_template')
    if type(count) is not int or count != 1 or spec.to_dict() != {k: fact[k] for k in ('schema_version', 'instance_id', 'minimum_power')} or not isinstance(template, Mapping):
        raise StateInvariantError('Casualty copy specification changed')
    return 1, deepcopy(dict(template))
