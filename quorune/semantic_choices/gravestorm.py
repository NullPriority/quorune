"""Gravestorm delegates every copy and target decision to the existing owner."""
from ..replacement.immutable import FrozenMap
from ..rules.gravestorm import decode_resolving_gravestorm
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation

GRAVESTORM_COUNT_FIELD = '_gravestorm_copies'


def prepare_gravestorm(effect, context):
    if set(effect) != {'op', 'player', 'gravestorm', 'stack', 'copy_source', GRAVESTORM_COUNT_FIELD}:
        raise SemanticChoiceError('Gravestorm requires its closed server-bound copy count')
    count = effect[GRAVESTORM_COUNT_FIELD]
    if type(count) is not int or count < 0:
        raise SemanticChoiceError('Gravestorm requires a nonnegative committed resolution count')
    if context.actor != context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Gravestorm requires its active resolving controller')
    try:
        source = decode_resolving_gravestorm(
            {key: value for key, value in effect.items() if key != GRAVESTORM_COUNT_FIELD},
            actor=context.actor, trigger_ref=context.stack_ref)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    copy = FrozenMap({'op': 'copy_stack_item', 'player': context.actor,
        'stack': source.item().ref, 'copy_source': source.to_dict()})
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue(reason='Create the Gravestorm copies of the original spell',
            prepend_effects=tuple(copy for _ in range(count))))
