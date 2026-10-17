from __future__ import annotations

"""A closed leaf for one explicitly retargetable instant/sorcery spell copy."""

import re
from ..stack_spell_copy_model import FixedStackSpellCopySpec


def fixed_stack_spell_copy_template(text):
    match = re.fullmatch(r'Copy target instant or sorcery spell(?P<controlled> you control)?\. '
        r'You may choose new targets for the copy\.', text.strip(), re.I)
    if match is None:
        return None
    spec = FixedStackSpellCopySpec(bool(match['controlled']))
    return ('fixed-instant-sorcery-copy-controlled-v1' if spec.controlled_only else 'fixed-instant-sorcery-copy-v1',
        spec.effects, spec.target_schema, spec.mechanics)
