from __future__ import annotations

"""Optional hand spell casts with the printed mana-value restrictions."""

from . import regex_working_set as re
from ..bounded_hand_cast_model import (
    BOUNDED_HAND_CAST_MECHANIC, BoundedHandCastSpec, LEGENDARY_WIZARD_HAND_CAST_CONDITION,
    SOURCE_COMBAT_DAMAGE_PLAYER_CONDITION, damaged_player_hand_cast_effect, legendary_wizard_hand_cast_effect,
    announced_x_hand_cast_effect,
)
from ..mana_value_limit import ManaValueLimitSpec
from ..replacement.immutable import FrozenMap


LEGENDARY_WIZARD_HAND_CAST_TEMPLATE = "legendary-wizard-bounded-hand-free-cast-v1"
_ANNOUNCED_X_DAMAGE_AND_CAST = re.compile(
    r"(?P<damage>.+? deals X damage to any target\.) You may cast a spell with mana value X or less "
    r"from your hand without paying its mana cost\.", re.I,
)


def announced_x_damage_hand_cast_spell_compiler(fallback):
    from ..rules.announced_x_damage_hand_cast import TEMPLATE, announced_x_damage_hand_cast_node_capabilities

    def compile_spell(text, **context):
        match = _ANNOUNCED_X_DAMAGE_AND_CAST.fullmatch(" ".join(text.split()))
        if match is not None:
            template, damage, target, mechanics = fallback(match["damage"], **context)
            effects = (*damage, announced_x_hand_cast_effect())
            if template is not None and announced_x_damage_hand_cast_node_capabilities(effects, target, None):
                return TEMPLATE, effects, target, (*mechanics, BOUNDED_HAND_CAST_MECHANIC)
        return fallback(text, **context)

    return compile_spell


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
