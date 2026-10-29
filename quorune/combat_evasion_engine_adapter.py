from __future__ import annotations

from typing import TYPE_CHECKING

from .characteristic_evaluation import type_parts
from .combat_evasion import (
    CombatantEvasionCharacteristics,
    CombatEvasionRuleError,
    CombatEvasionVerdict,
    combat_evasion_verdict,
)
from .keyword_abilities import normalized_characteristic_keywords
from .landwalk import BASIC_LAND_TYPES, LandwalkLandCharacteristics, LandwalkRuleError
from .semantic_runtime.block_restrictions import current_self_block_prohibitions
from .rules.landwalk_permissions import current_landwalk_block_exceptions
from .ring_effects import ring_power_block_allowed
from .ring_state import current_ring_rule_views
from .ring_model import RingObjectIdentity

if TYPE_CHECKING:
    from .engine import CommanderEngine
    from .model import CardInstance


def defending_basic_land_types(
    engine: CommanderEngine,
    defending_player: str,
) -> frozenset[str]:
    return frozenset(subtype for land in defending_land_characteristics(engine, defending_player)
        for subtype in land.subtypes if subtype in BASIC_LAND_TYPES)


def defending_land_characteristics(
    engine: CommanderEngine,
    defending_player: str,
) -> tuple[LandwalkLandCharacteristics, ...]:
    """Read the defender's current effective public battlefield land types."""

    if (
        not isinstance(defending_player, str)
        or not defending_player
        or defending_player not in engine.state.players
    ):
        raise LandwalkRuleError("Landwalk requires a current defending player")
    result = []
    for card in sorted(engine.state.cards.values(), key=lambda value: value.ref):
        if (
            card.zone != "battlefield"
            or card.phased_out
            or card.controller != defending_player
        ):
            continue
        data = engine._effective_card_data(card)
        type_line = data.get("type_line", "")
        if not isinstance(type_line, str):
            raise LandwalkRuleError("Effective type line must be a string")
        card_types, subtypes, supertypes = type_parts(type_line)
        if "land" in card_types:
            result.append(LandwalkLandCharacteristics(
                frozenset(card_types), frozenset(subtypes), frozenset(supertypes)))
    return tuple(result)


def engine_combat_evasion_verdict(
    engine: CommanderEngine,
    attacker: CardInstance,
    blocker: CardInstance,
    defending_player: str,
) -> CombatEvasionVerdict:
    """Compose the pure verdict from one narrow authoritative-state query."""

    if current_self_block_prohibitions(engine, blocker):
        return CombatEvasionVerdict(
            False,
            "blocker_has_self_counter_prohibition",
        )
    attacker_facts = combatant_evasion_characteristics(engine, attacker)
    blocker_facts = combatant_evasion_characteristics(engine, blocker)
    ordinary = combat_evasion_verdict(
        attacker_facts,
        blocker_facts,
        defending_land_characteristics(engine, defending_player),
        ignored_landwalk_keywords=current_landwalk_block_exceptions(engine, blocker),
    )
    if not ring_power_block_allowed(current_ring_rule_views(engine),
            attacker=RingObjectIdentity(attacker.object_id,attacker.ref,attacker.logical_object_id),
            attacker_power=attacker_facts.power,blocker_power=blocker_facts.power):
        return CombatEvasionVerdict(False,'external_greater_power_restriction')
    return ordinary


def combatant_evasion_characteristics(
    engine: CommanderEngine,
    card: CardInstance,
) -> CombatantEvasionCharacteristics:
    """Project one combatant's current public represented characteristics."""

    data = engine._effective_card_data(card)
    type_line = data.get("type_line", "")
    if not isinstance(type_line, str):
        raise CombatEvasionRuleError("Effective type line must be a string")
    raw_colors = data.get("colors", ())
    if not isinstance(raw_colors, (list, tuple, set, frozenset)) or any(
        not isinstance(color, str) or not color.strip()
        for color in raw_colors
    ):
        raise CombatEvasionRuleError("Effective colors are malformed")
    raw_power = data.get("power")
    try:
        int(str(raw_power))
    except (TypeError, ValueError):
        power = None
    else:
        power = engine._numeric_stat(card.object_id, "power")
    return CombatantEvasionCharacteristics(
        keywords=normalized_characteristic_keywords(data),
        colors=frozenset(color.strip().upper() for color in raw_colors),
        card_types=frozenset(type_parts(type_line)[0]),
        power=power,
    )


__all__ = [
    "combatant_evasion_characteristics",
    "defending_basic_land_types",
    "defending_land_characteristics",
    "engine_combat_evasion_verdict",
]
