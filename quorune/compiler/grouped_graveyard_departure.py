"""Compose fixed token creation with a live, controller-relative counter set."""

from ..rules.grouped_graveyard_departure import VARIANT, token_then_subtype_counter_dependencies
from . import regex_working_set as re
from .counter_placement_templates import fixed_counter_placement_set_effect_template
from .token_templates import fixed_token_creation_effect_template

_ORDERED_RESULT = re.compile(r"(?P<creation>Create .+? creature token), then (?P<counters>put .+)\.", re.I)


def grouped_graveyard_departure_result(binding):
    if binding.variant != VARIANT or binding.event.value != "card.leave_graveyard":
        return None
    match = _ORDERED_RESULT.fullmatch(binding.body)
    if match is None:
        return None
    token = fixed_token_creation_effect_template(match["creation"] + ".")
    counters = fixed_counter_placement_set_effect_template(match["counters"] + ".")
    if token is None or token.target_schema is not None or counters is None or counters.target_schema is not None:
        return None
    effects = (token.effect, *counters.effects)
    capabilities = token_then_subtype_counter_dependencies(effects)
    if not capabilities:
        return None
    return effects, (*token.mechanics, *counters.mechanics), capabilities
