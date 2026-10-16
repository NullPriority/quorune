from __future__ import annotations

"""Lower the complete Learn instruction, without its reminder text."""

from ..commander_learn_model import COMMANDER_LEARN_MECHANIC, commander_learn_instruction


def commander_learn_effect_template(text: str):
    if text.strip().casefold() != 'learn.':
        return None
    return ('commander-learn-v1', (commander_learn_instruction(),), None,
        (COMMANDER_LEARN_MECHANIC,))
