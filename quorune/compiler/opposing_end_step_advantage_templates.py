"""Lower the opposing end-step metric only with its fixed public/private result."""

from . import regex_working_set as re
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from .qualified_zone_event_bindings import public_binding_from_spec
from ..rules.opposing_end_step_advantage import COVERAGE, DEPENDENCIES, opposing_end_step_advantage_condition

_END_STEP = "at the beginning of each opponent's end step"
_TAPPED_SEARCH = re.compile(
    r"you may search your library for a basic (?:plains|island|swamp|mountain|forest) card, "
    r"put it onto the battlefield tapped, then shuffle\."
)


def opposing_end_step_advantage_binding(match):
    if match["prefix"].casefold().replace("’", "'") != _END_STEP:
        return None
    comparison = match["condition"].casefold()
    body = match["body"].casefold()
    if comparison == "that player controls more creatures than you" and body.startswith("create "):
        metric = "creature_count"
    elif comparison == "that player controls more lands than you" and _TAPPED_SEARCH.fullmatch(body):
        metric = "land_count"
    else:
        return None
    from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding, FixedCounterTriggerEvent

    return public_binding_from_spec(
        FixedPublicEventBindingSpec(
            event="step.begin", variant=COVERAGE, body=match["body"],
            template_id="opposing-end-step-count-advantage-v1",
            mechanic="cr-603-handling-triggered-abilities",
            condition=opposing_end_step_advantage_condition(metric),
            capabilities=DEPENDENCIES,
        ),
        binding_type=FixedCounterTriggerBinding, event_type=FixedCounterTriggerEvent,
    )
