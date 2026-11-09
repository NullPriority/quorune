"""Whole fixed-count controller ordering and explicit controller shuffle."""
import re

from .affected_player_discard_templates import fixed_affected_player_hand_top_effect_template
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..library_order_model import (
    FixedTopLibraryOrderSpec,
    FixedHandTopOrderSpec,
    GRAVEYARD_LIBRARY_ORDER_MECHANIC,
    GRAVEYARD_LIBRARY_ORDER_OPERATION,
    HAND_TOP_ORDER_MECHANIC,
    TOP_ORDER_MECHANIC,
    SHUFFLE_MECHANIC,
    fixed_shuffle_effect,
)
from ..rules.graveyard_card_targets import PublicGraveyardCardTargetKind, PublicGraveyardCardTargetSpec
from ..target_cardinality import AnnouncedTargetCardinality


_GRAVEYARD_ORDER = re.compile(
    r"Put (?:any number of|up to (?P<count>two|three|four|five|six)) target "
    r"(?P<quality>artifact |creature )?cards from (?P<origin>your|an opponent['’]s) graveyard "
    r"(?P<edge>on top of|on the bottom of) (?P<library>your|their) library"
    r"(?P<explicit_order> in any order)?\.?",
    re.IGNORECASE,
)


def _graveyard_library_order_template(text: str):
    match = _GRAVEYARD_ORDER.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    own = match["origin"].casefold() == "your"
    if match["library"].casefold() != ("your" if own else "their"):
        return None
    if not own and match["explicit_order"] is None:
        return None
    kind = PublicGraveyardCardTargetKind((match["quality"] or "").casefold() + "card")
    schema = PublicGraveyardCardTargetSpec(kind, "you" if own else "opponent").to_target_schema()
    schema.pop("count")
    if match["count"] is None:
        schema["count_parameter"] = AnnouncedTargetCardinality(
            selection="up_to", parameter="any_number", schema_version=2,
        ).to_dict()
    else:
        schema["up_to"] = fixed_number(match["count"])
    if not own:
        schema["same_owner"] = True
    effect = {
        "op": GRAVEYARD_LIBRARY_ORDER_OPERATION,
        "cards": "$targets",
        "position": "top" if match["edge"].casefold() == "on top of" else "bottom",
        "scope": "own" if own else "opponent",
    }
    return "ordered-graveyard-library-targets-v1", (effect,), schema, (
        GRAVEYARD_LIBRARY_ORDER_MECHANIC, "cr-115-targets",
    )


def fixed_top_order_template(text: str):
    match = re.fullmatch(
        rf'Look at the top (?P<count>{FIXED_COUNT_PATTERN}) cards? of your '
        r'library,? then put them back in any order\.',
        text.strip(),
        re.I,
    )
    if match is not None:
        try:
            spec = FixedTopLibraryOrderSpec(fixed_number(match['count']))
        except ValueError:
            return None
        return (
            'fixed-controller-top-order-v1', (spec.effect(),), None,
            (TOP_ORDER_MECHANIC,),
        )
    hand_top = re.fullmatch(
        rf"Put (?P<count>{FIXED_COUNT_PATTERN}) (?P<noun>cards?) from your hand "
        r"on top of your library(?: in any order)?\.",
        text.strip(), re.I,
    )
    if hand_top is not None:
        try:
            spec = FixedHandTopOrderSpec(fixed_number(hand_top["count"]))
        except ValueError:
            return None
        if (hand_top["noun"].casefold() == "card") != (spec.count == 1):
            return None
        return "fixed-hand-to-library-top-v1", (spec.effect(),), None, (HAND_TOP_ORDER_MECHANIC,)
    if re.fullmatch(r'Shuffle(?: your library)?\.', text.strip(), re.I):
        return (
            'fixed-controller-shuffle-v1', (fixed_shuffle_effect(),), None,
            (SHUFFLE_MECHANIC,),
        )
    return fixed_affected_player_hand_top_effect_template(text) or _graveyard_library_order_template(text)
