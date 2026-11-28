"""Lower the complete keyword sentence under its exact scheduled trigger."""

from ..rules.combat_keyword_share import CONDITION, MECHANIC, TEMPLATE, combat_keyword_share_effect
from ..replacement.immutable import FrozenMap


_BODY = (
    "creatures you control gain first strike until end of turn if a creature you control has first strike. "
    "the same is true for flying, deathtouch, double strike, haste, hexproof, indestructible, "
    "lifelink, menace, reach, skulk, trample, and vigilance."
)


def combat_keyword_share_template(binding, body):
    if (body != binding.body or " ".join(body.split()).casefold() != _BODY
            or (binding.event.value, binding.variant, binding.active_zone) != ("step.begin", "each combat", "battlefield")
            or FrozenMap(binding.event_condition or {}) != CONDITION):
        return None
    return TEMPLATE, (combat_keyword_share_effect(),), None, (MECHANIC,)
