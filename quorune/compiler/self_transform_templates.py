from __future__ import annotations

"""Source-pinned Transform instructions through the existing owner."""

from . import regex_working_set as re
from ..rules.source_references import SourceReferenceSpec
from ..rules.transform_capability_shapes import TRIGGERED_SOURCE_TRANSFORM_MECHANIC

SELF_TRANSFORM_MECHANIC = "fixed-activated-source-transform"


def fixed_self_transform_effect_template(text, *, card_name, triggered=False):
    subjects = "this creature|this artifact|this permanent|this land"
    if triggered:
        subjects += "|this enchantment"
    reference = SourceReferenceSpec(card_name).regex_pattern
    if re.fullmatch(rf"Transform (?:{subjects}|{reference})\.?", text.strip(), re.I) is None:
        return None
    mechanic = TRIGGERED_SOURCE_TRANSFORM_MECHANIC if triggered else SELF_TRANSFORM_MECHANIC
    return (f"{mechanic}-v1", (_source_transform_effect(),), None, (mechanic, "cr-701-transform"))


def _source_transform_effect():
    return {
        "op": "transform", "card": "$source.zone_object",
        "expected_transform_count": "$source.transform_count",
    }


def optional_equipped_source_transform_result(binding, body):
    if (
        (binding.event.value, binding.variant, binding.active_zone)
        != ("damage.dealt", "equipped_creature_combat_damage_a_player", "battlefield")
        or body != binding.body
        or body.casefold() != "you may transform this equipment."
    ):
        return None

    from ..rules.transform_capability_shapes import OPTIONAL_SOURCE_TRANSFORM_VERSION
    from .optional_effect_templates import FIXED_OPTIONAL_EFFECT_MECHANIC

    effect = {
        "op": "offer_optional_effect", "player": "$controller",
        "schema_version": OPTIONAL_SOURCE_TRANSFORM_VERSION,
        "effects": [_source_transform_effect()],
    }
    return ("equipped-optional-source-transform-v1", (effect,), None,
            (FIXED_OPTIONAL_EFFECT_MECHANIC, TRIGGERED_SOURCE_TRANSFORM_MECHANIC, "cr-701-transform"))
