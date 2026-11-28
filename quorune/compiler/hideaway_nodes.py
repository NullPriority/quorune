"""Lower the exact fixed-four Hideaway keyword into its ordinary entry trigger."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..hideaway_model import (
    HIDEAWAY_MECHANIC, HIDEAWAY_PLAY_MECHANIC, HIDEAWAY_PLAY_TEMPLATE, HIDEAWAY_REQUIRED_POWER,
    hideaway_play_effect, hideaway_selection_effect,
)
from ..rules.hideaway_library import HIDEAWAY_KEYWORD_TEMPLATE, HIDEAWAY_SELECTION_CAPABILITIES
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_PRINTED_HIDEAWAY = re.compile(
    r"Hideaway 4(?: \(When this land enters, look at the top four cards of your library, "
    r"exile one face down, then put the rest on the bottom in a random order\.\))?\.?$", re.I,
)
_PRINTED_PLAY = re.compile(
    r"You may play the exiled card without paying its mana cost if (?P<condition>.+)\.", re.I,
)


def hideaway_play_template(text):
    from .public_characteristic_aggregates import public_creature_power_threshold

    match = _PRINTED_PLAY.fullmatch(text)
    if match is None:
        return None
    condition = public_creature_power_threshold(match["condition"])
    if (condition is None or condition.amount != HIDEAWAY_REQUIRED_POWER or condition.aggregate.reduction != "sum"):
        return None
    return HIDEAWAY_PLAY_TEMPLATE, (hideaway_play_effect(),), None, (HIDEAWAY_PLAY_MECHANIC,)


def hideaway_keyword_node(*, node_id, line, material_line, span, capability_registry,
                          capability_profile, residuals, mechanics=()):
    if material_line.casefold().rstrip(".") != "hideaway 4" or not _PRINTED_HIDEAWAY.fullmatch(line):
        return None
    gate = explicit_capabilities_gate(
        HIDEAWAY_SELECTION_CAPABILITIES, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blocked = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Hideaway needs the private selection and exact linked exile authorities", blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event="permanent.enter.self", lowerable=True, exact=not blocked,
        template_id=HIDEAWAY_KEYWORD_TEMPLATE, effects=(hideaway_selection_effect(),),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=(HIDEAWAY_MECHANIC, "fixed-library-selection"), residual_ids=blocked,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
