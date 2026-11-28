"""Pair a controlled nontoken death exile with an optional finality upkeep return."""
from hashlib import sha256

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
from ..rules.linked_death_upkeep import (
    DEATH_CONDITION, DEATH_DEPENDENCIES, RETURN_DEPENDENCIES, UPKEEP_CONDITION, instruction,
)
from ..rules.source_references import SourceReferenceSpec
from . import regex_working_set as re
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual

_DEATH = re.compile(r"Whenever a nontoken creature you control dies, exile it\.", re.I)


def linked_death_upkeep_compiler(fallback, *, material_rows, card_name, permanent):
    if not permanent:
        return fallback
    source = SourceReferenceSpec(card_name).regex_pattern
    upkeep = re.compile(
        rf"At the beginning of your upkeep, you may put a creature card exiled with (?:this artifact|{source}) "
        r"onto the battlefield under your control with a finality counter on it\.", re.I,
    )
    exiles = [span.line for _, text, span in material_rows if _DEATH.fullmatch(text)]
    returns = [span.line for _, text, span in material_rows if upkeep.fullmatch(text)]
    if len(exiles) != 1 or len(returns) != 1:
        return fallback
    exile, = exiles
    returning, = returns
    pair = LINKED_ABILITY_BINDING_PREFIX + sha256(f"{card_name}:{exile}:{returning}".encode()).hexdigest()[:24]
    recipes = {
        exile: ("creature.dies", DEATH_CONDITION, DEATH_DEPENDENCIES, "exile"),
        returning: ("step.begin", UPKEEP_CONDITION, RETURN_DEPENDENCIES, "choose"),
    }

    def compile_trigger(**values):
        span = values["span"]
        recipe = recipes.get(span.line)
        if recipe is None:
            return fallback(**values)
        event, condition, capabilities, phase = recipe
        gate = explicit_capabilities_gate(capabilities, capability_registry=values["capability_registry"],
                                          capability_profile=values["capability_profile"])
        closure = gate.closure
        line = values["line"]
        blocked = (append_residual(values["residuals"], kind="dependency_contract", text=line, span=span,
            reason="The linked death and upkeep trigger requires its original exile source and controlled entry",
            blockers=gate.blockers),) if gate.blockers else ()
        return OracleNode(node_id=values["node_id"], kind="triggered_ability", text=line, span=span,
            active_zone="battlefield", event=event, event_condition=condition, lowerable=True, exact=not blocked,
            template_id=f"linked-death-upkeep-{phase}-v18", effects=(instruction(pair, phase),),
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), mechanics=("cr-603-handling-triggered-abilities",),
            residual_ids=blocked, capability_dependencies=gate.capabilities,
            capability_closure=closure.reachable if closure else (),
            capability_profile=closure.profile if closure else None,
            capability_fingerprint=closure.fingerprint if closure else None)

    return compile_trigger
