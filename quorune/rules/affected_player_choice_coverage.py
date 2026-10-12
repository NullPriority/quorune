from __future__ import annotations

"""Existing mechanic coverage for closed affected-player choice capabilities."""

from ..compiler.affected_player_discard_templates import (
    FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
    FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
    FIXED_CONTROLLER_DISCARD_CAPABILITY,
    FIXED_CONTROLLER_DISCARD_MECHANIC,
    FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY,
    FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC,
)
from ..compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC,
)
from .hand_inspection_capability_shapes import (
    FIXED_HAND_INSPECTION_CAPABILITY,
    FIXED_HAND_INSPECTION_MECHANIC,
)


def affected_player_choice_covered_mechanics(supplied: set[str]) -> set[str]:
    covered: set[str] = set()
    for capability, mechanics in (
        (FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY, {FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC, 'cr-402-hand'}),
        (FIXED_CONTROLLER_DISCARD_CAPABILITY, {FIXED_CONTROLLER_DISCARD_MECHANIC}),
        (FIXED_CONTEXT_PLAYER_DISCARD_CAPABILITY, {FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC}),
        ('zone.discard.whole_hand', {'whole-hand-discard', 'cr-402-hand'}),
        (FIXED_HAND_INSPECTION_CAPABILITY, {FIXED_HAND_INSPECTION_MECHANIC, 'cr-402-hand'}),
        (FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY, {FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, 'sacrifice'}),
    ):
        if capability in supplied:
            covered.update(mechanics)
    return covered
