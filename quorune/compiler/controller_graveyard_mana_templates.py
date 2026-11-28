"""Lower the printed live-controller graveyard threshold over canonical target operations."""

from . import regex_working_set as re
from ..rules.controller_graveyard_mana_targets import (
    CONTROLLER_GRAVEYARD_MANA_MECHANIC, controller_graveyard_mana_schema,
)


def controller_graveyard_mana_target_template(text):
    match = re.fullmatch(
        r"(?P<head>Counter target spell|Destroy target creature) with mana value less than or equal "
        r"to the number of cards in its controller['’]s graveyard\.", " ".join(text.split()), re.I,
    )
    if match is None:
        return None
    head = match["head"]
    if head.casefold().startswith("counter"):
        from .counter_templates import targeted_counter_effect_template

        base = targeted_counter_effect_template(head + ".")
    else:
        from .destruction_templates import targeted_destruction_effect_template

        base = targeted_destruction_effect_template(head + ".")
    template, effects, _, mechanics = base.compiled()
    return (template + "-live-controller-graveyard-v1", effects, controller_graveyard_mana_schema(effects[0]["op"]),
            (*mechanics, CONTROLLER_GRAVEYARD_MANA_MECHANIC))
