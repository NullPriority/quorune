"""Validate the sealed event result before delegating the unchanged v2 payment."""
from ..rules.life_gain_counted_results import life_gain_counter_payment_projection
from .fixed_effect_payment import prepare_fixed_effect_payment
from .model import SemanticChoiceError


def prepare_life_gain_counter_payment(effect, context):
    try:
        projected = life_gain_counter_payment_projection(effect, resolved=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor != context.stack_controller or projected['player'] != context.actor:
        raise SemanticChoiceError('Life-gain counter payment lost its ability controller')
    return prepare_fixed_effect_payment(projected, context)
