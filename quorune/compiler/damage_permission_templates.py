from __future__ import annotations

from . import regex_working_set as re
from ..damage_prevention_permission import (StaticUnpreventableDamageSpec,UNPREVENTABLE_HANDLER,
    UNPREVENTABLE_EVENT,UNPREVENTABLE_CAPABILITY)


def static_damage_unpreventability_handler(text):
    frames = ((r"Damage can't be prevented\.?",'global'),
        (r"Damage (?:that would be )?dealt by this creature can't be prevented\.?",'self'),
        (r"Combat damage (?:that would be )?dealt by creatures you control can't be prevented\.?",'controller_creatures_combat'))
    for pattern,scope in frames:
        if re.fullmatch(pattern,text.strip(),re.I):
            return (f'static-unpreventable-damage-{scope}-v1',
                {'handler_id':UNPREVENTABLE_HANDLER,'schema_version':1,'event':UNPREVENTABLE_EVENT,
                 'rule':StaticUnpreventableDamageSpec(scope).to_dict()},UNPREVENTABLE_CAPABILITY)
    return None


def turn_damage_prevention_template(text, *, card_name, source_is_permanent):
    from ..damage_prevention_permission import temporary_damage_prevention_prohibition
    from ..life_gain_permission import temporary_life_gain_effect
    from .damage_templates import FixedDamageRecipient, fixed_damage_effect_template

    if source_is_permanent is not False:
        return None
    normalized = " ".join(text.split())
    if re.fullmatch(r"Damage can't be prevented this turn\.?", normalized, re.I):
        return (
            "damage-turn-prevention-prohibition-v1",
            (temporary_damage_prevention_prohibition(),), None, (),
        )
    match = re.fullmatch(
        r"Players can't gain life this turn\. Damage can't be prevented this turn\. (?P<damage>.+)",
        normalized, re.I,
    )
    if match is None:
        return None
    damage, removed = re.subn(
        r" \(Shield counters don't prevent this damage as they're removed\.\)$",
        "", match.group("damage"), flags=re.I,
    )
    result = fixed_damage_effect_template(damage, card_name=card_name)
    if (
        result is None or result.target_spec is not None
        or result.recipient not in {FixedDamageRecipient.ANY_TARGET, FixedDamageRecipient.PLAYER_OR_PLANESWALKER}
        or removed and result.recipient is not FixedDamageRecipient.ANY_TARGET
    ):
        return None
    return (
        "damage-turn-life-and-prevention-prohibition-v1",
        (temporary_life_gain_effect("any"), temporary_damage_prevention_prohibition(), *result.effects),
        result.target_schema, (*result.mechanics, "cr-119-life"),
    )
