from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
import re
from .landwalk import QUALIFIED_LANDWALK_KEYWORDS
from .qualified_hexproof import QUALIFIED_HEXPROOF_KEYWORDS,QUALIFIED_HEXPROOF_CAPABILITY

from .ability_fragments import (
    CombatKeywordTriggerKind, CombatKeywordTriggerSpec,
    SpellCastKeywordTriggerKind, SpellCastKeywordTriggerSpec,
    ability_fragment_to_dict,
)


TRIGGERED_KEYWORD_GRANT_CAPABILITIES = {
    'Flanking': 'combat.trigger.flanking',
    'Exalted': 'combat.trigger.exalted',
    'Battle Cry': 'combat.trigger.battle_cry',
    'Melee': 'combat.trigger.melee',
    'Mentor': 'counter.producer.mentor',
    'Dethrone': 'counter.producer.dethrone',
    'Training': 'counter.producer.training',
    'Prowess': 'trigger.keyword.prowess',
}


def triggered_keyword_grant(value: str):
    """Return a canonical label, fragment and owner for one closed keyword."""
    if type(value) is not str:
        return None
    label = ' '.join(value.strip().split()).title()
    if label == 'Prowess':
        fragment = SpellCastKeywordTriggerSpec(kind=SpellCastKeywordTriggerKind.PROWESS)
    elif label in TRIGGERED_KEYWORD_GRANT_CAPABILITIES:
        fragment = CombatKeywordTriggerSpec(
            kind=CombatKeywordTriggerKind(label.casefold().replace(' ', '_')), amount=1,
        )
    else:
        match = re.fullmatch(r'Bushido ([1-9]\d*)', label)
        if match is None:
            return None
        fragment = CombatKeywordTriggerSpec(kind=CombatKeywordTriggerKind.BUSHIDO, amount=int(match[1]))
        return label, ability_fragment_to_dict(fragment), 'combat.trigger.bushido'
    return label, ability_fragment_to_dict(fragment), TRIGGERED_KEYWORD_GRANT_CAPABILITIES[label]


FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES: Mapping[
    str, tuple[str, ...]
] = {
    "Deathtouch": (
        "combat.damage.assignment.deathtouch",
        "damage.result.deathtouch",
    ),
    "Defender": ("combat.attack.defender",),
    "Double Strike": ("combat.damage.participation.strike_steps",),
    "Fear": ("combat.block.fear",),
    "First Strike": ("combat.damage.participation.strike_steps",),
    "Flying": ("combat.block.flying",),
    "Haste": (
        "activation.tap_untap_cost.haste",
        "combat.attack.haste",
    ),
    "Hexproof": ("target.protection.hexproof_permanent",),
    "Horsemanship": ("combat.block.horsemanship",),
    "Plainswalk": ("combat.block.landwalk.basic_type",),
    "Islandwalk": ("combat.block.landwalk.basic_type",),
    "Swampwalk": ("combat.block.landwalk.basic_type",),
    "Mountainwalk": ("combat.block.landwalk.basic_type",),
    "Forestwalk": ("combat.block.landwalk.basic_type",),
    "Indestructible": ("permanent.indestructible.ordinary",),
    "Infect": ("damage.result.infect",),
    "Intimidate": ("combat.block.intimidate",),
    "Lifelink": ("damage.result.lifelink",),
    "Menace": ("combat.block.menace",),
    "Reach": ("combat.block.reach",),
    "Shadow": ("combat.block.shadow",),
    "Shroud": ("target.protection.shroud_permanent",),
    "Skulk": ("combat.block.skulk",),
    "Trample": ("combat.damage.assignment.trample",),
    "Vigilance": ("combat.attack.vigilance",),
    "Wither": ("damage.result.wither",),
}
FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES = {
    **FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,
    **{keyword.title(): ("combat.block.landwalk.qualified_type",)
        for keyword in QUALIFIED_LANDWALK_KEYWORDS},
    **{keyword.title():(QUALIFIED_HEXPROOF_CAPABILITY,) for keyword in QUALIFIED_HEXPROOF_KEYWORDS},
}
FIXED_CHARACTERISTIC_KEYWORDS = frozenset(
    FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
)


class EffectiveKeywordError(ValueError):
    """The represented current keyword snapshot is malformed."""


class EffectiveKeywordHost(Protocol):
    """Read-only characteristic port shared by keyword rule owners."""

    def _effective_card_data(
        self,
        card: Any,
        *,
        ignore_face_down: bool = False,
    ) -> Mapping[str, Any]: ...


def normalized_effective_keywords(
    host: EffectiveKeywordHost,
    card: Any,
    *,
    ignore_face_down: bool = False,
) -> frozenset[str]:
    """Return current represented keywords case-insensitively and redundantly."""

    return normalized_characteristic_keywords(
        host._effective_card_data(
            card,
            ignore_face_down=ignore_face_down,
        )
    )


def normalized_characteristic_keywords(
    data: Mapping[str, Any],
) -> frozenset[str]:
    """Normalize one already-computed effective-characteristic snapshot."""

    if not isinstance(data, Mapping):
        raise EffectiveKeywordError(
            "Effective characteristics must be a mapping"
        )
    raw_keywords = data.get("keywords", ())
    if not isinstance(raw_keywords, (list, tuple, set, frozenset)) or any(
        not isinstance(keyword, str) or not keyword.strip()
        for keyword in raw_keywords
    ):
        raise EffectiveKeywordError("Effective keywords are malformed")
    return frozenset(keyword.strip().casefold() for keyword in raw_keywords)


__all__ = [
    "EffectiveKeywordError",
    "EffectiveKeywordHost",
    "FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES",
    "FIXED_CHARACTERISTIC_KEYWORDS",
    "normalized_characteristic_keywords",
    "normalized_effective_keywords",
]
