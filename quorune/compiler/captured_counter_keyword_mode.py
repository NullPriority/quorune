"""Closed controlled-land-entry counter and keyword branch."""

from . import regex_working_set as re
from ..rules.captured_counter_keyword_grant import (
    CAPTURED_COUNTER_KEYWORD_MECHANICS, CAPTURED_LEGENDARY_CAST_KEYWORDS,
    captured_counter_keyword_instruction, captured_legendary_counter_keyword_instruction,
    captured_legendary_counter_keyword_mechanics, legendary_counter_keyword_cast_condition,
)


_BODY = re.compile(
    r"Put a \+1/\+1 counter on each creature you control\. "
    r"Those creatures gain vigilance until end of turn\.", re.I,
)


def captured_counter_keyword_mode(header, body, *, card_name):
    if _BODY.fullmatch(" ".join(body.split())) is None:
        return None
    from .fixed_counter_trigger_nodes import fixed_counter_trigger_binding

    binding = fixed_counter_trigger_binding(header, card_name=card_name)
    if binding is None or (binding.event.value, binding.variant) != ("land.enter", "controlled_land"):
        return None
    return (
        "captured-counter-keyword-grant-v1", (captured_counter_keyword_instruction(),),
        None, CAPTURED_COUNTER_KEYWORD_MECHANICS,
    )



_LEGENDARY_CAST_BODY = re.compile(
    r"Put a \+1/\+1 counter on each legendary creature you control\. "
    r"They gain (?P<keyword>vigilance|menace) until end of turn\.", re.I,
)


def captured_legendary_cast_counter_result(binding, body):
    if (binding.event.value, binding.active_zone) != ("spell.cast", "battlefield") or body != binding.body:
        return None
    match = _LEGENDARY_CAST_BODY.fullmatch(" ".join(body.split()))
    if match is None:
        return None
    keyword = match["keyword"].title()
    for color, result in CAPTURED_LEGENDARY_CAST_KEYWORDS.items():
        if keyword == result and binding.event_condition == legendary_counter_keyword_cast_condition(color):
            return (
                "captured-legendary-cast-counter-keyword-v3",
                (captured_legendary_counter_keyword_instruction(color),), None,
                captured_legendary_counter_keyword_mechanics(keyword),
            ), True
    return None
