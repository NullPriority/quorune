"""Fixed non-target land untap instructions, distinct from target grammar."""
import re

from ..untargeted_untap_model import FixedLandUntapChoiceSpec, UNTARGETED_UNTAP_MECHANIC
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


def fixed_land_untap_choice_template(text: str):
    match = re.fullmatch(
        rf'(?:You )?Untap up to (?P<maximum>{FIXED_COUNT_PATTERN}) '
        r'lands?(?P<scope> you control)?\.', text.strip(), re.I,
    )
    if match is None:
        return None
    try:
        spec = FixedLandUntapChoiceSpec(
            fixed_number(match['maximum']),
            'controller' if match['scope'] else 'any',
        )
    except ValueError:
        return None
    return (
        'fixed-untargeted-land-untap-v1', (spec.effect(),), None,
        ('tap-and-untap', UNTARGETED_UNTAP_MECHANIC),
    )
