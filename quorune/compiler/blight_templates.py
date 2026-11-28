from __future__ import annotations

"""Fixed controller Blight and its printed draw sequence."""

from . import regex_working_set as re

from ..rules.blight_model import BLIGHT_MECHANIC, BLIGHT_OPERATION
from .fixed_numbers import FIXED_COUNT_PATTERN


def fixed_blight_effect_template(text):
    match = re.fullmatch(r"Blight (?P<amount>[1-9][0-9]*)\.?", text.strip(), re.I)
    if match is None:
        return None
    return (
        "fixed-controller-blight-v1",
        ({"op": BLIGHT_OPERATION, "schema_version": 1, "player": "$controller", "amount": int(match["amount"])},),
        None,
        (BLIGHT_MECHANIC,),
    )


def normalized_blight_sequence(text):
    match = re.fullmatch(
        rf"(?P<draw>(?:You )?draw (?:{FIXED_COUNT_PATTERN}) cards?) and (?P<blight>blight [1-9][0-9]*)\.",
        text.strip(), re.I,
    )
    return f"{match['draw']}. {match['blight']}." if match is not None else text


def opponent_blight_entry_spec(material_line, *, card_name):
    from .fixed_public_event_trigger_bindings import _source_subject, _spec
    from ..creature_subtypes import canonical_creature_subtype
    from ..rules.opponent_blight import OPPONENT_BLIGHT_ENTRY_VARIANT, opponent_blight_entry_condition

    match = re.fullmatch(
        r"Whenever (?P<source>.+?) or another (?P<subtype>[A-Za-z][A-Za-z' -]*) you control enters, "
        r"(?P<body>each opponent blights 1[.]?)",
        material_line, re.IGNORECASE,
    )
    if match is None or not _source_subject(match["source"], card_name=card_name):
        return None
    subtype = canonical_creature_subtype(match["subtype"])
    if subtype is None:
        return None
    return _spec(
        "permanent.enter", OPPONENT_BLIGHT_ENTRY_VARIANT, match["body"],
        "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
        condition=opponent_blight_entry_condition(subtype),
    )
