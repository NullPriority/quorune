"""Compile the complete normalized attack-ability observation."""

from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.attack_trigger_copy import DEPENDENCIES, EVENT, TEMPLATE, condition, effects
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern


_PREFIX = "Whenever a creature you control attacking causes a triggered ability of that creature to trigger, "


def attack_trigger_copy_node(*, node_id, line, material_line, span,
                             source_name, capability_registry, capability_profile, residuals):
    if not material_line.casefold().startswith(_PREFIX.casefold()):
        return None
    self_ref = rf"(?:{SourceReferenceSpec(source_name).regex_pattern}|{source_self_permanent_pattern()})"
    if not re.fullmatch(
        rf"{_PREFIX}put a quest counter on {self_ref}\. Then if it has four or more quest counters on it, "
        r"you may copy that ability\. You may choose new targets for the copy\.",
        material_line, re.IGNORECASE,
    ):
        return None
    gate = explicit_capabilities_gate(DEPENDENCIES, capability_registry=capability_registry,
                                      capability_profile=capability_profile)
    blocked = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Attack-trigger copying needs its declared occurrence, quest counter and stack-copy owners.",
        blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event=EVENT, event_condition=condition(), effects=effects(), lowerable=True, exact=not blocked,
        template_id=TEMPLATE, residual_ids=blocked, runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=("cr-603-handling-triggered-abilities", "trigger-event-normalized-public-action",
                   "cr-122-counters", "cr-707-copying-objects"),
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
