"""Read an exact cast occurrence's ordinal without later casts changing it."""

from ..public_history_query_model import matching_public_history_events
from ..spell_history_ordinal_model import QualifiedSpellOrdinalSpec


def qualified_spell_cast_number(state,condition,context):
    spec=QualifiedSpellOrdinalSpec.from_condition(condition)
    caster=context.get('controller');identity=context.get('logical_object_id');types=context.get('types')
    if type(caster) is not str or caster not in state.players or type(identity) is not str or not identity:
        raise ValueError('A spell ordinal requires its exact committed caster and incarnation')
    if not isinstance(types,(list,tuple)) or any(type(value) is not str for value in types):raise ValueError('A spell ordinal requires sealed card types')
    history=state.turn_history
    matches=matching_public_history_events(state,caster,spec.query)
    if matches is None:raise ValueError('Qualified spell history is unavailable')
    occurrences=[event for event in history.events if event.kind=='spell_cast' and event.actor==caster and event.object_incarnation==identity]
    if len(occurrences)!=1 or tuple(sorted(set(types)))!=occurrences[0].types:
        raise ValueError('Spell ordinal occurrence is absent, ambiguous or disagrees with sealed history')
    for number,event in enumerate(matches,1):
        if event.object_incarnation==identity:return number
    return 0
