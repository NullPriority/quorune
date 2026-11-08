from __future__ import annotations

import re

from ..announced_damage import (
    ANNOUNCED_DAMAGE_EFFECT_FIELD,
    DAMAGE_ALLOCATION_FIELD,
    DamageAllocationSpec,
)
from .damage_templates import (
    FixedDamageEffectTemplate,
    fixed_damage_effect_template,
    source_pronoun_damage_effect_template,
)


_DIVIDED_DAMAGE = re.compile(
    r"(?P<source>.+?) deals (?P<amount>[1-9][0-9]*) damage divided as you choose among "
    r"(?P<count>one or two|one, two, or three|any number of) (?P<domain>.+?)\.?",
    re.IGNORECASE,
)
_DIRECT_RECIPIENTS = {
    "targets": "any target",
    "target creatures": "target creature",
    "target creatures with flying": "target creature with flying",
    "target creatures and/or planeswalkers": "target creature or planeswalker",
    "target creatures and/or planeswalkers your opponents control": "target creature or planeswalker an opponent controls",
    "target white and/or blue creatures": "target white or blue creature",
    "target attacking or blocking creatures": "target attacking or blocking creature",
}


def fixed_announced_damage_template(text: str, *, card_name: str, allow_source_pronoun: bool = False):
    match = _DIVIDED_DAMAGE.fullmatch(text.strip())
    if match is None:
        return None
    recipient = _DIRECT_RECIPIENTS.get(match["domain"].casefold())
    if recipient is None:
        return None
    amount = int(match["amount"])
    direct_body = f"{match['source']} deals {amount} damage to {recipient}."
    direct = fixed_damage_effect_template(direct_body, card_name=card_name)
    if direct is None and allow_source_pronoun:
        direct = source_pronoun_damage_effect_template(direct_body)
    if not isinstance(direct, FixedDamageEffectTemplate) or direct.target_schema is None:
        return None
    count = match["count"].casefold()
    minimum, maximum = {
        "one or two": (1, 2),
        "one, two, or three": (1, 3),
        "any number of": (0, amount),
    }[count]
    group = dict(direct.target_schema)
    del group["count"]
    group.update({"min": minimum, "max": min(maximum, amount)})
    schema = {
        "groups": [group],
        DAMAGE_ALLOCATION_FIELD: DamageAllocationSpec(amount).to_dict(),
    }
    effect = {
        "op": "damage_fixed_set",
        "source": "$source",
        "amount": amount,
        ANNOUNCED_DAMAGE_EFFECT_FIELD: True,
    }
    return "damage-announced-allocation-v1", (effect,), schema, direct.mechanics
