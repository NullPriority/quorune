"""Compose the existing keyword leaves with one printed same-target Ferocious result."""

from ..resolution_conditions import RESOLUTION_CONDITION_MECHANIC, RESOLUTION_CONDITION_OPERATION
from ..rules.ferocious_keyword_append import (
    FEROCIOUS_KEYWORD_TEMPLATE, ferocious_keyword_append_capabilities,
)
from . import regex_working_set as re
from .public_state_queries import fixed_public_state_condition


_KEYWORD_APPEND = re.compile(
    r"(?P<prefix>Target creature gains double strike until end of turn\.) "
    r"Ferocious — That creature also (?P<child>gains trample until end of turn) "
    r"if (?P<condition>you control a creature with power 4 or greater)\.",
    re.I,
)


def ferocious_keyword_append_template(text, *, source_name, compile_component):
    match = _KEYWORD_APPEND.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    mandatory = compile_component(match["prefix"])
    conditional = compile_component("Target creature " + match["child"] + ".")
    condition = fixed_public_state_condition(match["condition"], source_name=source_name)
    if mandatory[0] is None or conditional[0] is None or mandatory[2] != conditional[2] or condition is None:
        return None
    wrapper = {
        "op": RESOLUTION_CONDITION_OPERATION, "player": "$controller", "condition": condition.to_dict(),
        "effects": list(conditional[1]), "mechanic_ids": list(conditional[3]),
        "prefix_mechanic_ids": list(mandatory[3]),
    }
    effects = (*mandatory[1], wrapper)
    mechanics = tuple(dict.fromkeys((
        RESOLUTION_CONDITION_MECHANIC, *mandatory[3], *conditional[3],
    )))
    if not ferocious_keyword_append_capabilities(effects=effects, target_schema=mandatory[2], mechanic_ids=mechanics):
        return None
    return FEROCIOUS_KEYWORD_TEMPLATE, effects, mandatory[2], mechanics
