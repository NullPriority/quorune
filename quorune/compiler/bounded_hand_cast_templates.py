from __future__ import annotations

"""Optional hand spell casts with the printed mana-value restrictions."""

from . import regex_working_set as re
from ..bounded_hand_cast_model import (
    BOUNDED_HAND_CAST_MECHANIC, BoundedHandCastSpec, LEGENDARY_WIZARD_HAND_CAST_CONDITION,
    SOURCE_COMBAT_DAMAGE_PLAYER_CONDITION, damaged_player_hand_cast_effect, legendary_wizard_hand_cast_effect,
)
from ..mana_value_limit import ManaValueLimitSpec
from ..replacement.immutable import FrozenMap


LEGENDARY_WIZARD_HAND_CAST_TEMPLATE = "legendary-wizard-bounded-hand-free-cast-v1"


def damaged_player_hand_cast_trigger_result(binding, body):
    if ((binding.event.value, binding.variant, binding.active_zone)
            != ("damage.dealt.self", "source_combat_damage_player", "battlefield")
            or body != binding.body
            or FrozenMap(binding.event_condition or {}) != SOURCE_COMBAT_DAMAGE_PLAYER_CONDITION
            or re.fullmatch(
                r"Look at that player['’]s hand\. You may cast a spell from among those cards "
                r"without paying its mana cost\.", " ".join(body.split()), re.I,
            ) is None):
        return None
    return (
        "damaged-player-inspected-hand-cast-v1", (damaged_player_hand_cast_effect(),),
        None, (BOUNDED_HAND_CAST_MECHANIC,),
    ), True


def bounded_hand_cast_template(text):
    match = re.fullmatch(r'You may cast a spell with mana value (?P<maximum>0|[1-9][0-9]*) or less '
        r'from your hand without paying its mana cost\.', ' '.join(text.strip().split()), re.I)
    if match is None:
        return None
    return BoundedHandCastSpec(ManaValueLimitSpec(int(match['maximum'])))


def legendary_wizard_hand_cast_trigger_result(binding, body):
    if (body != binding.body or binding.event.value != "step.begin" or binding.active_zone != "battlefield"
            or FrozenMap(binding.event_condition or {}) != LEGENDARY_WIZARD_HAND_CAST_CONDITION):
        return None
    if not re.fullmatch(
        r"You may cast an instant or sorcery spell with mana value X or less from your hand without paying "
        r"its mana cost, where X is twice the number of legendary Wizards you control\.",
        " ".join(body.split()),
        re.I,
    ):
        return None
    return (
        LEGENDARY_WIZARD_HAND_CAST_TEMPLATE, (legendary_wizard_hand_cast_effect(),), None,
        (BOUNDED_HAND_CAST_MECHANIC,),
    ), True
