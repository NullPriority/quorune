"""Optional wrappers retain the bound event/source context of their child."""
import re

from .optional_effect_templates import fixed_optional_effect_template


def binding_aware_optional_result(body: str, *, compile_child):
    if not re.match(r'you may\b', body, re.I) or re.match(r'you may pay\b', body, re.I):
        return None
    current = False

    def compile_result(text):
        nonlocal current
        result, needs_current = compile_child(text)
        current |= needs_current
        return result

    optional = fixed_optional_effect_template(body, compile_effect=compile_result)
    return (optional.compiled(), current) if optional is not None else None
