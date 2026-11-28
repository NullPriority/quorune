"""Lower a closed death counter copy and its required owner-library shuffle."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.death_counter_shuffle import (
    DEATH_COUNTER_SHUFFLE_DEPENDENCIES,
    death_counter_shuffle_effects,
    death_counter_shuffle_target_schema,
)
from ..rules.source_references import SourceReferenceSpec
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual
from . import regex_working_set as re


_BODY = re.compile(
    r"when (?P<subject>.+?) dies, put (?:its counters|the counters it had) on target creature you control"
    r"(?:,|\.) then shuffle this card into its owner['’]s library\.?",
    re.IGNORECASE,
)


def source_death_counter_shuffle_node(*, node_id, line, material_line, span, card_name,
                                     capability_registry, capability_profile, residuals):
    match = _BODY.fullmatch(material_line)
    if match is None or (match["subject"].casefold() != "this creature"
                         and not SourceReferenceSpec(card_name).matches(match["subject"])):
        return None
    gate = explicit_capabilities_gate(
        DEATH_COUNTER_SHUFFLE_DEPENDENCIES,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blocked = (append_residual(
        residuals, kind="dependency_contract", text=line, span=span,
        reason="Departure counter placement and owner shuffle require their original target and effect owners",
        blockers=gate.blockers,
    ),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event="creature.dies.self", lowerable=True, exact=not blocked,
        template_id="source-death-counter-owner-shuffle-v1",
        effects=death_counter_shuffle_effects(), target_schema=death_counter_shuffle_target_schema(),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        mechanics=("cr-603-handling-triggered-abilities",),
        residual_ids=blocked, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None,
        capability_fingerprint=closure.fingerprint if closure else None,
    )
