from __future__ import annotations

from typing import Any, Protocol

from ...abilities import ActivatedAbility, CostChoice
from ...ability_fragments import (
    canonical_ability_fragments,
    granted_activated_specs,
)
from ...card_overrides.game_record_v3 import (
    historical_game_record_v3_activated_abilities,
)
from ...fixed_mana_abilities import FixedManaMode
from ...util import normalize_mana_bundle


class ActivatedAbilityQueryHost(Protocol):
    semantics: Any

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...


def activated_abilities(
    host: ActivatedAbilityQueryHost,
    card: Any,
) -> tuple[ActivatedAbility, ...]:
    """Return compiler-pinned, intrinsic, and typed granted abilities."""

    data = host._effective_card_data(card)
    raw_abilities = data.get("activated_abilities", ())
    if not isinstance(raw_abilities, (list, tuple)):
        raise ValueError("activated_abilities must be an array")
    abilities = [
        value
        if isinstance(value, ActivatedAbility)
        else ActivatedAbility.from_dict(value)
        for value in raw_abilities
    ]
    if (
        not abilities
        and bool(
            getattr(
                getattr(host, "semantics", None),
                "runtime_handler_compatibility_enabled",
                False,
            )
        )
    ):
        abilities.extend(
            historical_game_record_v3_activated_abilities(card, data)
        )
    abilities.sort(key=lambda ability: ability.line_index)
    abilities.extend(_typed_granted_abilities(data))
    return tuple(abilities)


def _typed_granted_abilities(
    data: dict[str, Any],
) -> tuple[ActivatedAbility, ...]:
    specs = granted_activated_specs(
        canonical_ability_fragments(data.get("ability_fragments", ()))
    )
    counts: dict[str, int] = {}
    result: list[ActivatedAbility] = []
    for index, spec in enumerate(specs):
        ordinal = counts.get(spec.ability_id, 0) + 1
        counts[spec.ability_id] = ordinal
        result.append(
            ActivatedAbility(
                ability_id=(
                    spec.ability_id
                    if ordinal == 1
                    else f"{spec.ability_id}#{ordinal}"
                ),
                line_index=25_000 + index,
                oracle_line=f"{spec.cost_text}: {spec.effect_text}",
                cost_text=spec.cost_text,
                effect_text=spec.effect_text,
                zones=("battlefield",),
                mana={
                    key: spec.mana_bundle.get(key, 0)
                    for key in ("GENERIC", "W", "U", "B", "R", "G", "C")
                },
                tap_source=spec.tap_source,
                untap_source=spec.untap_source,
                sacrifice_source=spec.sacrifice_source,
                life_payment=spec.life_payment,
                choices=tuple(
                    CostChoice.from_dict(choice) for choice in spec.choices
                ),
                sorcery_speed=spec.sorcery_speed,
                mana_ability=spec.mana_ability,
                fixed_mana_outputs=tuple(
                    FixedManaMode.from_bundle(dict(output))
                    for output in spec.fixed_mana_outputs
                ),
                builtin_semantic_key=spec.semantic_key,
                mana_spend_restriction=spec.mana_spend_restriction,
            )
        )
    return tuple(result)


__all__ = ["ActivatedAbilityQueryHost", "activated_abilities"]
