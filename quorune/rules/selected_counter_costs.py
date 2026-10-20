from __future__ import annotations

"""One +1/+1 counter from a selected creature the activator controls."""

from collections.abc import Mapping

SELECTED_PLUS_COUNTER_COST_KIND = 'remove_one_plus_one_counter'
SELECTED_COUNTER_COST_CAPABILITY = 'activation.selected_counter_removal.fixed'


def validate_selected_counter_choice(choice):
    if (choice.kind != SELECTED_PLUS_COUNTER_COST_KIND or type(choice.count) is not int
            or choice.count != 1 or choice.zone != 'battlefield' or choice.card_type != 'creature'
            or choice.another is not False or choice.predicate is not None):
        raise ValueError('Selected counter costs require one controlled creature and one +1/+1 counter')


def selected_counter_cost_candidates(host, actor, choice):
    from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
    from ..object_query import object_query_result, query_objects
    validate_selected_counter_choice(choice)
    rows = []
    for object_id in host.state.players[actor].zones['battlefield']:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
        rows.append(object_query_result(card, data,
            type_parts=host._type_parts(str(data.get('type_line') or '')),
            known_to_actor=True, attached_to_ref=None))
    query = ObjectQuerySpec(zones=('battlefield',), controller=actor, types_all=('creature',),
        state_predicate=PermanentStatePredicateSpec(counter_name='+1/+1', minimum_counter_count=1))
    return tuple(row.ref for row in query_objects(rows, query))


def pay_selected_counter_cost(host, actor, source, ability, response):
    from ..counter_removal import CounterRemoval, commit_counter_removals, plan_counter_removals
    if len(ability.choices) != 1:
        raise ValueError('Selected counter costs cannot combine selectable payment groups')
    choice = ability.choices[0]
    validate_selected_counter_choice(choice)
    if ('cost_cards' in response and 'cost_objects' in response
            and response['cost_cards'] != response['cost_objects']):
        raise ValueError('Selected counter payment aliases disagree')
    refs = response.get('cost_cards', response.get('cost_objects'))
    if not isinstance(refs, (list, tuple)) or len(refs) != 1 or type(refs[0]) is not str:
        raise ValueError('Choose exactly one creature to pay the counter cost')
    if refs[0] not in selected_counter_cost_candidates(host, actor, choice):
        raise ValueError('The selected creature cannot pay this counter cost')
    card = next(host.state.cards[object_id] for object_id in host.state.players[actor].zones['battlefield']
                if host.state.cards[object_id].ref == refs[0])
    plan = plan_counter_removals(host, (CounterRemoval(object_id=card.object_id,
        counter_name='+1/+1', amount=1, expected_zone='battlefield',
        expected_logical_object_id=card.logical_object_id),))
    commit_counter_removals(host, plan)
    return [card.object_id]


def selected_counter_cost_choice_schema(host, actor, source, ability):
    choices = tuple(choice for choice in ability.choices if choice.kind == SELECTED_PLUS_COUNTER_COST_KIND)
    if not choices:
        return None
    if len(choices) != 1 or len(ability.choices) != 1:
        raise ValueError('Selected counter costs require one isolated public selection')
    return {'cost_cards': {'type': 'array', 'label': 'Creature to remove a +1/+1 counter from',
        'legal_refs': list(selected_counter_cost_candidates(host, actor, choices[0])),
        'minimum': 1, 'maximum': 1}}


def selected_counter_runtime_dependencies(program):
    schema = getattr(program, 'cost_schema', None)
    if not isinstance(schema, Mapping):
        return ()
    choices = schema.get('choices') or ()
    selected = [choice for choice in choices if isinstance(choice, Mapping)
                and choice.get('k') == SELECTED_PLUS_COUNTER_COST_KIND]
    if not selected:
        return ()
    if (len(selected) != 1 or len(choices) != 1 or set(selected[0]) != {'k', 'n', 'z', 't'}
            or type(selected[0]['n']) is not int or selected[0]['n'] != 1
            or selected[0]['z'] != 'battlefield' or selected[0]['t'] != 'creature'
            or getattr(program, 'event', None) != 'activate'):
        raise ValueError('Selected counter payment requires its complete activation cost owner')
    return (SELECTED_COUNTER_COST_CAPABILITY,)
