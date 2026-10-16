from __future__ import annotations

"""Learn action identity and its existing discard/draw ownership."""

from ..commander_learn_model import (
    COMMANDER_LEARN_CAPABILITY, COMMANDER_LEARN_MECHANIC,
    validate_commander_learn_instruction,
)


def commander_learn_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (COMMANDER_LEARN_MECHANIC not in mechanic_ids
            or target_schema is not None or len(effects) != 1):
        return ()
    try:
        validate_commander_learn_instruction(effects[0], player='$controller')
    except (TypeError, ValueError):
        return ()
    return (COMMANDER_LEARN_CAPABILITY, 'choice.affected_player.fixed_discard',
        'effect.choice.optional_fixed_mana_payment', 'zone.change.destination_replacement',
        'zone.draw.library_to_hand')
