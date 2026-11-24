"""Compose a simultaneous attack counter group with a restriction on its other target."""

from . import regex_working_set as re
from .counter_placement_group_templates import fixed_counter_placement_group_effect_template
from ..rules.attack_counter_unblockable import (
    ATTACK_COUNTER_UNBLOCKABLE_CONDITION, ATTACK_COUNTER_UNBLOCKABLE_MECHANIC,
)
from ..rules.source_references import SourceReferenceSpec
from ..rules.temporary_declaration_restrictions import temporary_declaration_restriction


_BODY = re.compile(
    r"Put a \+1/\+1 counter on (?P<source>[^.]+?) and a \+1/\+1 counter on "
    r"up to one other target attacking creature\. That creature can't be blocked this turn\.",
    re.IGNORECASE,
)


def attack_counter_unblockable_result(binding, body, *, card_name):
    if (binding.event.value != "creature.attacks" or binding.variant != "source_attacks"
            or binding.active_zone != "battlefield" or binding.event_condition != ATTACK_COUNTER_UNBLOCKABLE_CONDITION
            or body != binding.body):
        return None
    match = _BODY.fullmatch(" ".join(body.split()))
    if match is None:
        return None
    source = match["source"]
    if source.casefold() not in {"it", "this creature"} and not SourceReferenceSpec(card_name).matches(source):
        return None
    placement = fixed_counter_placement_group_effect_template(
        "Put a +1/+1 counter on this creature and a +1/+1 counter on up to one other target attacking creature.",
        card_name=card_name,
        source_is_permanent=True,
    )
    if placement is None:
        return None
    unblockable = temporary_declaration_restriction("unblockable")
    restriction = {"op": "grant_declaration_restriction_until_end_of_turn",
                   "cards": "$targets", "restriction": "unblockable"}
    mechanics = tuple(dict.fromkeys(("cr-122-counters", "cr-115-targets", "cr-611-continuous-effects",
                                     *unblockable.mechanics, ATTACK_COUNTER_UNBLOCKABLE_MECHANIC)))
    return ("fixed-attack-optional-target-counter-unblockable-v1",
            (*placement.effects, restriction), placement.target_schema, mechanics)
