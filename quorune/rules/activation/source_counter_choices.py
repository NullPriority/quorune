from __future__ import annotations

"""Public exact distributions for fixed one/two any-kind source counter costs."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

from ...counter_names import normalized_counter_name
from ...util import stable_json
from ..activation_counter_cost import SourceCounterRemovalCost

ANY_SOURCE_COUNTER_COST_CAPABILITY = 'activation.source_counter_removal.any_kind'
SOURCE_COUNTER_PAYMENT_FIELD = 'source_counter_payment'


@dataclass(frozen=True, slots=True)
class SourceCounterPaymentChoice:
    choice_id: str
    label: str
    removals: tuple[tuple[str, int], ...]


def source_counter_payment_choices(source, cost):
    if cost is None or cost.schema_version != 2:
        return ()
    if source.zone != 'battlefield' or source.phased_out:
        return ()
    if any(type(name) is not str or normalized_counter_name(name) != name
           or type(amount) is not int or amount < 0 for name, amount in source.counters.items()):
        raise ValueError('Source counter payment state must be canonical')
    names = sorted(name for name, amount in source.counters.items() if amount > 0)
    distributions = []
    if cost.amount == 1:
        distributions = [((name, 1),) for name in names]
    else:
        for index, first in enumerate(names):
            for second in names[index:]:
                if first == second:
                    if source.counters[first] >= 2:
                        distributions.append(((first, 2),))
                else:
                    distributions.append(((first, 1), (second, 1)))
    choices = []
    for removals in distributions:
        identity = {'source_identity': source.logical_object_id, 'removals': removals}
        identifier = 'source-counter:' + hashlib.sha256(stable_json(identity).encode('utf8')).hexdigest()
        label = 'Remove ' + ' and '.join(f'{amount} {name} counter' + ('s' if amount != 1 else '')
                                       for name, amount in removals)
        choices.append(SourceCounterPaymentChoice(identifier, label, removals))
    return tuple(choices)


def selected_source_counter_removals(source, cost, response):
    if not isinstance(response, Mapping) or type(response.get(SOURCE_COUNTER_PAYMENT_FIELD)) is not str:
        raise ValueError('Choose the source counters to remove')
    choice = next((choice for choice in source_counter_payment_choices(source, cost)
        if choice.choice_id == response[SOURCE_COUNTER_PAYMENT_FIELD]), None)
    if choice is None:
        raise ValueError('The selected source counter payment is unavailable or stale')
    return choice.removals


def source_counter_cost_choice_schema(source, ability):
    cost = ability.source_counter_removal_cost
    if cost is None or cost.schema_version != 2:
        return None
    choices = source_counter_payment_choices(source, cost)
    return {SOURCE_COUNTER_PAYMENT_FIELD: {'label': 'Counters to remove',
        'legal_values': [choice.choice_id for choice in choices],
        'option_labels': {choice.choice_id: choice.label for choice in choices}}}


def any_source_counter_runtime_dependencies(program):
    schema = getattr(program, 'cost_schema', None)
    if not isinstance(schema, Mapping):
        return ()
    raw = schema.get('source_counter_removal_cost')
    if not isinstance(raw, Mapping) or raw.get('schema_version') != 2:
        return ()
    SourceCounterRemovalCost.from_dict(raw)
    if (getattr(program, 'event', None) != 'activate' or schema.get('choices')
            or schema.get('sacrifice_source') or schema.get('discard_source') or schema.get('exile_source')):
        raise ValueError('Any-kind source counter costs require their isolated activation contract')
    return (ANY_SOURCE_COUNTER_COST_CAPABILITY,)
