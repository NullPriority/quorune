"""Existing condition coordinator selects an ordinary sealed paid-cost result."""
from ..paid_alternative_cast_model import PaidAlternativeCastFact
from ..rules.paid_alternative_cast_facts import validate_paid_alternative_instruction
from ..replacement.immutable import FrozenMap
from .model import AutoContinue,SemanticChoiceError,SemanticChoicePreparation


def prepare_paid_alternative_result(effect,context):
    from .resolution_result_validation import validate_resolution_result_children
    try:
        requirement,children=validate_paid_alternative_instruction(effect,allow_bound=True)
        if effect['player']!=context.actor or context.actor!=context.stack_controller:
            raise ValueError('Paid result controller changed')
        fact=PaidAlternativeCastFact.from_dict(effect['cast_fact'])
        validate_resolution_result_children(effect,children,(),context)
        matched=fact.satisfies(requirement)
    except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
        auto_continue=AutoContinue(reason='Sealed alternative-cost payment selected its ordinary result',
            prepend_effects=tuple(FrozenMap(c) for c in children) if matched else ()))
