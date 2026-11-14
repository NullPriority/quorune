"""Continue a current conditional branch through the existing effect cursor."""
from ..conditional_target_preparation import validate_preparation_instruction
from ..conditional_target_preparation import preparation_branch_capabilities
from ..replacement.immutable import FrozenMap
from .model import SemanticChoiceError, SemanticChoicePreparation, AutoContinue


def prepare_conditional_target(effect, context):
    try:
        condition, effects = validate_preparation_instruction(effect)
        if effect['player'] != context.actor or context.actor != context.stack_controller:
            raise ValueError('Conditional preparation requires the original resolving controller')
        for branch, ids in (('effects', 'mechanic_ids'), ('otherwise_effects', 'otherwise_mechanic_ids')):
            if effect[branch] and not preparation_branch_capabilities(effect[branch], effect[ids], effect['target_binding'], subject=effect['condition_target']):
                raise ValueError('Conditional preparation child lacks its represented result owner')
        matched = context.query.resolution_condition_matches(condition.to_dict())
        branch = effects if matched else effect['otherwise_effects']
        if effect['condition_target'] is None:branch = ()
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason='current bound target preparation', prepend_effects=tuple(FrozenMap(e) for e in branch)))
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
