"""Source-spanned optional discard experience and ordinary experience modifiers."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.experience_results import (
    EXPERIENCE_ATTACK_TEMPLATE, EXPERIENCE_MECHANIC, EXPERIENCE_PUMP_TEMPLATE,
    experience_attack_condition, experience_attack_dependencies, experience_attack_effects, experience_pump_effect,
)
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


def experience_attack_node(*, node_id, line, material_line, span, source_name,
                           capability_registry, capability_profile, residuals):
    names = SourceReferenceSpec(source_name).regex_pattern
    if not re.fullmatch(
        rf"Whenever (?:{names}|this creature) attacks, you may discard a card\. Then you get "
        r"an experience counter for each player who discarded a card this turn\.",
        " ".join(material_line.split()), re.I,
    ):
        return None
    gate = explicit_capabilities_gate(
        experience_attack_dependencies(), capability_registry=capability_registry, capability_profile=capability_profile,
    )
    blocked = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
                               reason="Attack experience requires its completed discard choice and current history authority",
                               blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, event="creature.attacks",
        active_zone="battlefield", event_condition=experience_attack_condition(), effects=experience_attack_effects(),
        lowerable=True, exact=not blocked, template_id=EXPERIENCE_ATTACK_TEMPLATE, residual_ids=blocked,
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), mechanics=(EXPERIENCE_MECHANIC, "cr-122-counters"),
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None, capability_fingerprint=closure.fingerprint if closure else None,
    )


def experience_pump_template(body, source_name):
    names = SourceReferenceSpec(source_name).regex_pattern
    if not re.fullmatch(
        rf"Until end of turn, (?:{names}|this creature) gets \+1/\+1 for each experience counter you have and gains menace\.",
        " ".join(body.split()), re.I,
    ):
        return None
    return EXPERIENCE_PUMP_TEMPLATE, (experience_pump_effect(),), None, (EXPERIENCE_MECHANIC,)
