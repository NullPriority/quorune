from __future__ import annotations

"""Complete public-player hand-count grammar, retaining its direct target."""

from . import regex_working_set as re

from ..rules.source_references import SourceReferenceSpec
from ..scalar_effect_amount_model import (
    SCALAR_AMOUNT_MECHANIC, TARGET_HAND_COUNT_MECHANIC, ScalarAmountOrigin, ScalarEffectAmountSpec,
    direct_player_amount_target_is_closed,
)
from ..rules.target_hand_count_shapes import (
    UPKEEP_TARGET_HAND_LIFE_TEMPLATE, UPKEEP_TARGET_HAND_LIFE_VARIANT,
    upkeep_target_hand_life_condition, upkeep_target_hand_life_effect, upkeep_target_hand_life_schema,
)


_COUNT=r'the number of cards in (?:that player[\x27’]s|their) hand'
_UPKEEP_HAND_LIFE = re.compile(
    r"At the beginning of your upkeep, (?P<body>you gain 1 life for each card in target opponent['’]s hand\.)",
    re.I,
)


def upkeep_target_hand_life_binding_spec(text):
    match = _UPKEEP_HAND_LIFE.fullmatch(text)
    if match is None:
        return None
    from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec

    return FixedPublicEventBindingSpec(
        event="step.begin", variant=UPKEEP_TARGET_HAND_LIFE_VARIANT, body=match["body"],
        template_id=UPKEEP_TARGET_HAND_LIFE_TEMPLATE, mechanic="cr-603-handling-triggered-abilities",
        condition=upkeep_target_hand_life_condition(),
        capabilities=("life.change.effect", "target.revalidate_resolution"),
    )


def upkeep_target_hand_life_result(binding, body):
    if binding.variant != UPKEEP_TARGET_HAND_LIFE_VARIANT or body != binding.body:
        return None
    return (binding.template_id, (upkeep_target_hand_life_effect(),), upkeep_target_hand_life_schema(),
            ("cr-119-life", "cr-115-targets", SCALAR_AMOUNT_MECHANIC, "declared-effect-amount",
             TARGET_HAND_COUNT_MECHANIC)), True


def target_hand_count_producer(value,schema):
    if direct_player_amount_target_is_closed(schema) and re.fullmatch(_COUNT,value,re.I):
        return ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET_HAND_COUNT,schema_version=4)
    return None


def normalized_target_hand_damage(text,source_name):
    source=rf'(?:{SourceReferenceSpec(source_name).regex_pattern}|this (?:artifact|creature|enchantment|permanent))'
    match=re.fullmatch(rf'(?P<source>{source}) deals damage to (?P<recipient>target (?:player|opponent)) equal to (?P<count>{_COUNT})\.',text,re.I)
    if match is None:
        return text
    # The scalar assembler adds the one final sentence terminator.
    return f"{match['source']} deals damage equal to {match['count']} to {match['recipient']}"
