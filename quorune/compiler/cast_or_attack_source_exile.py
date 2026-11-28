"""Lower the two public occurrences with one exact linked source result."""

from . import regex_working_set as re
from ..rules.cast_or_attack_source_exile import (
    DEPENDENCIES, TEMPLATE, VARIANT, cast_or_attack_source_exile_condition,
    is_closed_next_end_step_self_exile,
)
from .fixed_public_event_trigger_bindings import _spec
from .linked_exile_return_templates import linked_exile_return_effect_template


_CAST_OR_ATTACK = re.compile(
    r"(?:When|Whenever) a player casts a spell or a creature attacks, (?P<body>.+)", re.IGNORECASE,
)


def cast_or_attack_source_exile_result(body, *, card_name):
    compiled = linked_exile_return_effect_template(body, card_name=card_name, source_is_permanent=True)
    if compiled is None or not is_closed_next_end_step_self_exile(compiled[1], compiled[2], compiled[3]):
        return None
    return compiled


def cast_or_attack_source_exile_binding(material_line, *, card_name):
    match = _CAST_OR_ATTACK.fullmatch(material_line)
    if match is None or not card_name or cast_or_attack_source_exile_result(match["body"], card_name=card_name) is None:
        return None
    return _spec(
        "spell.cast", VARIANT, match["body"], TEMPLATE, "trigger-event-normalized-spell-cast",
        condition=cast_or_attack_source_exile_condition(), capabilities=DEPENDENCIES,
    )
