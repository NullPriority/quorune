from __future__ import annotations

"""Typed executable abilities for represented predefined-token profiles."""

from enum import Enum
from typing import Any, Mapping

from .abilities import ActivatedAbility
from .fixed_mana_abilities import FixedManaMode
from .replacement.immutable import FrozenMap
from .util import normalize_mana_bundle


class StandardTokenAbilityProfile(str, Enum):
    TAP_SAC_ANY_COLOR_MANA = "tap_sac_any_color_mana_v1"
    TWO_TAP_SAC_GAIN_THREE_LIFE = "two_tap_sac_gain_three_life_v1"
    TWO_SAC_DRAW_CARD = "two_sac_draw_card_v1"
    ONE_TAP_SAC_EXPLORE_CONTROLLED_CREATURE = (
        "one_tap_sac_explore_controlled_creature_v1"
    )
    TAP_COLORLESS_RESTRICTED = "tap_colorless_restricted_v1"
    TAP_SAC_IMPULSE_ONE = "tap_sac_impulse_one_v1"


TOKEN_ABILITY_PROFILE_FIELD = "activated_ability_profile"


def _any_color_mana() -> ActivatedAbility:
    return ActivatedAbility(
        ability_id="ab1",
        line_index=0,
        oracle_line="{T}, Sacrifice this token: Add one mana of any color.",
        cost_text="{T}, Sacrifice this token",
        effect_text="Add one mana of any color.",
        zones=("battlefield",),
        mana=normalize_mana_bundle(None),
        tap_source=True,
        sacrifice_source=True,
        mana_ability=True,
        fixed_mana_outputs=tuple(
            FixedManaMode.from_bundle({color: 1}) for color in "WUBRG"
        ),
    )


def _gain_three_life() -> ActivatedAbility:
    return ActivatedAbility(
        ability_id="ab1",
        line_index=0,
        oracle_line="{2}, {T}, Sacrifice this token: You gain 3 life.",
        cost_text="{2}, {T}, Sacrifice this token",
        effect_text="You gain 3 life.",
        zones=("battlefield",),
        mana={"GENERIC": 2},
        tap_source=True,
        sacrifice_source=True,
        builtin_semantic_key="builtin:gain-life:3",
    )


def _draw_card() -> ActivatedAbility:
    return ActivatedAbility(
        ability_id="ab1",
        line_index=0,
        oracle_line="{2}, Sacrifice this token: Draw a card.",
        cost_text="{2}, Sacrifice this token",
        effect_text="Draw a card.",
        zones=("battlefield",),
        mana={"GENERIC": 2},
        sacrifice_source=True,
        builtin_semantic_key="builtin:draw:1",
    )


def _explore_controlled_creature() -> ActivatedAbility:
    return ActivatedAbility(
        ability_id="ab1",
        line_index=0,
        oracle_line=(
            "{1}, {T}, Sacrifice this token: Target creature you control "
            "explores. Activate only as a sorcery."
        ),
        cost_text="{1}, {T}, Sacrifice this token",
        effect_text=(
            "Target creature you control explores. Activate only as a sorcery."
        ),
        zones=("battlefield",),
        mana={"GENERIC": 1},
        tap_source=True,
        sacrifice_source=True,
        sorcery_speed=True,
        builtin_semantic_key="builtin:explore-target",
        target_schema=FrozenMap(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "controller": "you",
                "creature": True,
                "count": 1,
            }
        ),
    )


def _restricted_colorless_mana() -> ActivatedAbility:
    return ActivatedAbility(
        ability_id="ab1",
        line_index=0,
        oracle_line=(
            "{T}: Add {C}. This mana can't be spent to cast a nonartifact "
            "spell."
        ),
        cost_text="{T}",
        effect_text=(
            "Add {C}. This mana can't be spent to cast a nonartifact spell."
        ),
        zones=("battlefield",),
        mana=normalize_mana_bundle(None),
        tap_source=True,
        mana_ability=True,
        fixed_mana_outputs=(FixedManaMode.from_bundle({"C": 1}),),
        mana_spend_restriction="nonartifact_spell_prohibited",
    )


def _junk_impulse_one() -> ActivatedAbility:
    return ActivatedAbility(
        ability_id="ab1",
        line_index=0,
        oracle_line=(
            "{T}, Sacrifice this token: Exile the top card of your library. "
            "You may play that card this turn. Activate only as a sorcery."
        ),
        cost_text="{T}, Sacrifice this token",
        effect_text=(
            "Exile the top card of your library. You may play that card this "
            "turn. Activate only as a sorcery."
        ),
        zones=("battlefield",),
        mana=normalize_mana_bundle(None),
        tap_source=True,
        sacrifice_source=True,
        sorcery_speed=True,
        builtin_semantic_key="builtin:fixed-impulse-access:1:turn",
    )


_STANDARD_TOKEN_ABILITIES = {
    StandardTokenAbilityProfile.TAP_SAC_ANY_COLOR_MANA: (
        _any_color_mana(),
    ),
    StandardTokenAbilityProfile.TWO_TAP_SAC_GAIN_THREE_LIFE: (
        _gain_three_life(),
    ),
    StandardTokenAbilityProfile.TWO_SAC_DRAW_CARD: (
        _draw_card(),
    ),
    StandardTokenAbilityProfile.ONE_TAP_SAC_EXPLORE_CONTROLLED_CREATURE: (
        _explore_controlled_creature(),
    ),
    StandardTokenAbilityProfile.TAP_COLORLESS_RESTRICTED: (
        _restricted_colorless_mana(),
    ),
    StandardTokenAbilityProfile.TAP_SAC_IMPULSE_ONE: (
        _junk_impulse_one(),
    ),
}


def standard_token_characteristics(
    characteristics: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Lower an explicit typed profile without inspecting a token name."""

    result = dict(characteristics or {})
    raw_profile = result.pop(TOKEN_ABILITY_PROFILE_FIELD, None)
    if raw_profile is None:
        return result
    if "activated_abilities" in result:
        raise ValueError(
            "Token ability profiles cannot compete with explicit abilities"
        )
    try:
        profile = StandardTokenAbilityProfile(raw_profile)
    except (TypeError, ValueError) as exc:
        raise ValueError("Unsupported token ability profile") from exc
    abilities = _STANDARD_TOKEN_ABILITIES[profile]
    result["activated_abilities"] = [ability.to_dict() for ability in abilities]
    return result


__all__ = [
    "StandardTokenAbilityProfile",
    "TOKEN_ABILITY_PROFILE_FIELD",
    "standard_token_characteristics",
]
