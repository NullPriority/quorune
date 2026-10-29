"""The existing condition coordinator selects a sealed declared-cost branch."""
from ..declared_cost_conditions import validate_declared_cost_instruction
from ..replacement.immutable import FrozenMap
from .model import AutoContinue, SemanticChoiceError, SemanticChoicePreparation


def prepare_declared_cost_condition(effect, context):
    from .resolution_result_validation import validate_resolution_result_children
    try:
        condition, effects = validate_declared_cost_instruction(effect)
        if effect['player'] != context.actor or context.actor != context.stack_controller:
            raise ValueError('Declared-cost condition controller changed')
        otherwise = effect['otherwise_effects']
        validate_resolution_result_children(effect, effects, otherwise, context)
        matched = condition.matches(effect['cast_fact'])
    except (ValueError, TypeError, KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue(reason='Linked casting declaration selected its ordinary result',
            prepend_effects=tuple(FrozenMap(child) for child in (effects if matched else otherwise))))
