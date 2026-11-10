from __future__ import annotations

"""Lower one counter followed by closed results for the same object's controller."""

import re

from ..rules.stack_counter_followup import (
    STACK_COUNTER_FOLLOWUP_OPERATION, STACK_TARGET_CONTROLLER, stack_counter_followup_node_capabilities,
)
from .affected_player_discard_templates import fixed_controller_discard_effect_template
from .counter_templates import targeted_counter_effect_template


_FOLLOWUP = re.compile(r"Its controller (?P<verb>mills|loses|draws|discards) (?P<body>.+)", re.I)


def stack_counter_followup_template(text, *, compile_component):
    if text.lstrip()[:7].casefold() != "counter":
        return None
    prefix, separator, tail = " ".join(text.split()).partition(". ")
    if not separator or not prefix.casefold().startswith("counter target "):
        return None
    counter = targeted_counter_effect_template(prefix + ".")
    if counter is None or counter.counter_destination is not None:
        return None
    tail, second = _split_followups(tail)
    match = _FOLLOWUP.fullmatch(tail)
    if match is None:
        return None
    verb = match["verb"].casefold().removesuffix("s")
    body = f"{verb.capitalize()} {match['body']}"
    if verb == "discard":
        discard = fixed_controller_discard_effect_template(body)
        first = discard.compiled() if discard is not None else None
    else:
        first = compile_component(body)
    if first is None or first[0] is None or first[2] is not None or len(first[1]) != 1:
        return None
    leaf = first[1][0]
    recipient = ({"players": [STACK_TARGET_CONTROLLER]} if verb == "discard" else {"player": STACK_TARGET_CONTROLLER})
    followups = [{**leaf, **recipient}]
    mechanics = (*counter.mechanics, *first[3])
    if second is not None:
        compiled = compile_component(second)
        if compiled[0] is None or compiled[2] is not None or len(compiled[1]) != 1:
            return None
        followups.append(compiled[1][0])
        mechanics = (*mechanics, *compiled[3])
    effects = ({"op": STACK_COUNTER_FOLLOWUP_OPERATION, "schema_version": 1,
                "stack": "$target.0", "followups": followups},)
    if not stack_counter_followup_node_capabilities(
        effects=effects, target_schema=counter.target_schema, mechanic_ids=mechanics,
    ):
        return None
    return "fixed-stack-counter-controller-followup-v1", effects, counter.target_schema, tuple(dict.fromkeys(mechanics))


def _split_followups(tail):
    loss = re.fullmatch(r"(?P<first>Its controller loses .+?) and (?P<second>you gain .+?\.)", tail, re.I)
    if loss is not None:
        return loss["first"] + ".", loss["second"]
    first, separator, second = tail.partition(". ")
    return (first + ".", second) if separator else (tail, None)
