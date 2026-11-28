"""Compile a complete untargeted end-step sacrifice and shared-type payment."""

from . import regex_working_set as re
from ..replacement.immutable import FrozenMap
from ..rules.event_optional_draw_shapes import controller_end_step_condition
from ..rules.shared_type_sacrifice import SHARED_TYPE_SACRIFICE_TEMPLATE, shared_type_sacrifice_effect
from .affected_player_sacrifice_templates import FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC


_BODY = re.compile(
    r"You may sacrifice an artifact, creature, enchantment, land, or planeswalker\. "
    r"If you do, each opponent may sacrifice a permanent of their choice that shares a card type with it\. "
    r"For each opponent who doesn['’]t, that player loses 2 life and you draw a card\.", re.I,
)


def shared_type_sacrifice_binding_result(binding, body):
    if (
        (binding.event.value, binding.active_zone, binding.variant) != ("step.begin", "battlefield", "your end step")
        or FrozenMap(binding.event_condition) != FrozenMap(controller_end_step_condition())
        or body != binding.body or _BODY.fullmatch(" ".join(body.split())) is None
    ):
        return None
    return ((SHARED_TYPE_SACRIFICE_TEMPLATE, (shared_type_sacrifice_effect(),), None,
             (FIXED_AFFECTED_PLAYER_SACRIFICE_MECHANIC, "sacrifice")), False)
