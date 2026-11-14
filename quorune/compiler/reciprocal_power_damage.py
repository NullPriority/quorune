"""Recognize two complete power-damage instructions without converting to Fight."""
import re

from ..reciprocal_power_damage import RECIPROCAL_POWER_MECHANIC, reciprocal_power_damage_capabilities
from ..rules.source_references import SourceReferenceSpec


def reciprocal_power_damage_template(text, *, card_name, allow_source_pronoun=False):
    match = re.fullmatch(r'(?P<first>[^.]+)\. That creature deals damage equal to its power to (?P<source>[^.]+)\.',
        text.strip(), re.I)
    if match is None:
        return None
    source = match['source']
    if source.casefold() != 'this creature' and not SourceReferenceSpec(card_name).matches(source):
        return None
    from .creature_power_damage_templates import fixed_creature_power_damage_effect_template, FixedCreaturePowerDamageTemplate

    first = fixed_creature_power_damage_effect_template(match['first'] + '.', card_name=card_name,
        allow_source_pronoun=allow_source_pronoun)
    if first is None or len(first.effects) != 1:
        return None
    second = {'op': 'creature_power_damage', 'kind': 'bite', 'source': '$target.0', 'target': '$source',
        'target_must_be_creature': True, 'source_lki': None, 'schema_version': 4, 'recipient_is_original_source': True}
    effects = (*first.effects, second)
    mechanics = (*first.mechanics, RECIPROCAL_POWER_MECHANIC)
    if not reciprocal_power_damage_capabilities(effects=effects, target_schema=first.target_schema, mechanic_ids=mechanics):
        return None
    return FixedCreaturePowerDamageTemplate('sequential-source-reciprocal-power-damage-v1', effects, first.target_schema, mechanics)
