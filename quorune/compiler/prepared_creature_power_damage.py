"""Compose fixed target preparation with its one-way creature-power damage."""
import re
from typing import Any, Callable, Mapping

from ..targets import TargetGroup
from ..rules.prepared_creature_power_damage_shapes import (
    PREPARED_POWER_DAMAGE_MECHANIC,
    prepared_creature_power_damage_capabilities,
)
from .creature_power_damage_templates import fixed_creature_power_damage_effect_template

Compiled = tuple[str | None, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]]
_SEQUENCE = re.compile(
    r'^(?P<preparation>[^.]+?)(?:\. (?:Then )?|, then )(?:it|that creature) '
    r'deals damage equal to its power to (?P<recipient>[^.]+)\.?$', re.IGNORECASE,
)
_SUBJECT = re.compile(r'\btarget (?P<quality>(?:green |[A-Za-z\'’-]+ )?creature) you control\b', re.IGNORECASE)


def prepared_creature_power_damage_template(
    text: str, *, source_name: str, compile_atomic: Callable[[str], Compiled],
) -> Compiled | None:
    match = _SEQUENCE.fullmatch(text)
    if match is None:
        return None
    preparation_text = match['preparation']
    subjects = list(_SUBJECT.finditer(preparation_text))
    if len(subjects) != 1:
        return None
    preparation = compile_atomic(preparation_text + '.')
    if preparation[0] is None or preparation[2] is None:
        return None
    bite = fixed_creature_power_damage_effect_template(
        f"Target {subjects[0]['quality']} you control deals damage equal to its power to {match['recipient']}.",
        card_name=source_name,
    )
    if bite is None:
        return None
    try:
        first = dict(bite.target_schema['groups'][0])
        first.pop('id')
        if TargetGroup.from_mapping(preparation[2]) != TargetGroup.from_mapping(first):
            return None
    except (TypeError, ValueError):
        return None
    effects = (*preparation[1], *bite.effects)
    mechanics = tuple(sorted({*preparation[3], *bite.mechanics, PREPARED_POWER_DAMAGE_MECHANIC}))
    if not prepared_creature_power_damage_capabilities(
        effects=effects, target_schema=bite.target_schema, mechanic_ids=mechanics,
    ):
        return None
    return 'prepared-target-creature-power-damage-v1', effects, bite.target_schema, mechanics
