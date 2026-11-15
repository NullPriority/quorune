"""Optional wrappers retain the bound event/source context of their child."""
import re

from .optional_effect_templates import fixed_optional_effect_template


def binding_aware_optional_result(body: str, *, compile_child):
    from .fixed_effect_payment_templates import is_optional_resolution_payment
    linked_payment = is_optional_resolution_payment(body)
    if not re.match(r'you may\b', body, re.I) or re.match(r'you may pay\b', body, re.I):
        return None
    current = False

    def compile_result(text):
        nonlocal current
        result, needs_current = compile_child(text)
        current |= needs_current
        return result

    optional = fixed_optional_effect_template(body, compile_effect=compile_result)
    if optional is not None and linked_payment:
        # A successful mandatory discard under the outer optional result owns
        # its own cost/if-paid boundary, including the once-per-turn action use.
        from ..fixed_effect_payment import FixedEffectPaymentSpec
        children = optional.effects[0]['effects']
        try:
            child, = children
            payment = FixedEffectPaymentSpec.from_dict(child.get('payment'), allow_mandatory=True)
            if (child.get('op') != 'offer_optional_mana_payment' or child.get('player') != '$controller'
                    or not payment.mandatory or payment.predicate.owner != '$controller'):
                return None
        except (TypeError, ValueError, AttributeError):
            return None
    return (optional.compiled(), current) if optional is not None else None
