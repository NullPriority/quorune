from __future__ import annotations

"""Closed source-self combat growth bodies over normalized public events."""

import re
from typing import Any, Mapping


SOURCE_ZONE_OBJECT = "$source.zone_object"
FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS = frozenset(
    {
        "fixed-source-combat-growth-counter-v1",
        "fixed-source-combat-growth-stats-v1",
    }
)

_FIXED_STATS = re.compile(
    r"(?:it|this creature) gets "
    r"(?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) until end of turn\.?",
    re.IGNORECASE,
)
_FIXED_COUNTER = re.compile(
    r"put a \+1/\+1 counter on it\.?",
    re.IGNORECASE,
)
_ADMITTED_BINDINGS = frozenset(
    {
        ("creature.attacks", "source_attacks"),
        ("creature.blocks", "this_creature_blocks"),
        ("creature.blocks", "this_creature_blocks_flying"),
        ("creature.becomes_blocked", "this_creature_becomes_blocked"),
        ("damage.dealt.self", "source_combat_damage_player"),
    }
)

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]


def fixed_source_combat_growth_effect_template(
    text: str,
    *,
    event: str,
    variant: str,
) -> CompiledEffectTemplate:
    """Lower one mandatory fixed self-growth body for an admitted event."""

    if (event, variant) not in _ADMITTED_BINDINGS:
        return None, (), None, ()
    normalized = text.strip()
    stats = _FIXED_STATS.fullmatch(normalized)
    if stats is not None:
        power = int(stats.group("power"))
        toughness = int(stats.group("toughness"))
        if power == 0 and toughness == 0:
            return None, (), None, ()
        return (
            "fixed-source-combat-growth-stats-v1",
            (
                {
                    "op": "modify_stats_until_end_of_turn",
                    "card": SOURCE_ZONE_OBJECT,
                    "power": power,
                    "toughness": toughness,
                },
            ),
            None,
            ("cr-611-continuous-effects",),
        )
    if _FIXED_COUNTER.fullmatch(normalized) is not None:
        return (
            "fixed-source-combat-growth-counter-v1",
            (
                {
                    "op": "place_counters",
                    "card": SOURCE_ZONE_OBJECT,
                    "counter": "+1/+1",
                    "amount": 1,
                    "source": "$source",
                },
            ),
            None,
            ("cr-122-counters",),
        )
    return None, (), None, ()


__all__ = [
    "FIXED_SOURCE_COMBAT_GROWTH_TEMPLATE_IDS",
    "SOURCE_ZONE_OBJECT",
    "fixed_source_combat_growth_effect_template",
]
