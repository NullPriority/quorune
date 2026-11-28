"""Compile a cast-entry opponent copy and named-artifact own-turn echoes."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.named_artifact_echo import (
    COVERAGE, ENTRY_DEPS, DRAW_DEPS, LIFE_DEPS, OPPONENT, entry_copy_effect,
    echo_effect, named_artifact_condition,
)
from ..source_entry_cast_information import source_entry_cast_condition, source_entry_cast_runtime_coverage
from . import regex_working_set as re
from .ir_model import OracleNode, append_residual
from .dependency_gate import explicit_capabilities_gate

_ENTRY = "when this artifact enters, if it was cast, target opponent creates a token that's a copy of it."
_ECHO = re.compile(r"Whenever an opponent who controls an artifact named (?P<name>.+?) "
                   r"(?P<action>draws a card|gains life) during their turn, "
                   r"(?P<result>you draw a card|you gain that much life)\.", re.I)


def named_artifact_echo_compiler(fallback, *, material_rows, permanent, card_types):
    if not permanent or "artifact" not in card_types:
        return fallback
    recipes = {}
    for _, row, span in material_rows:
        if row.casefold() == _ENTRY:
            condition = source_entry_cast_condition(any_caster=True)
            recipes[span.line] = ("permanent.enter.self", condition, ENTRY_DEPS, entry_copy_effect(), OPPONENT)
        elif match := _ECHO.fullmatch(row):
            draw = match["action"].casefold() == "draws a card"
            if match["result"].casefold() != ("you draw a card" if draw else "you gain that much life"):
                continue
            event = "card.drawn" if draw else "life.gained"
            recipes[span.line] = (event, named_artifact_condition(match["name"]),
                                  DRAW_DEPS if draw else LIFE_DEPS, echo_effect(event), None)

    def compile_trigger(**values):
        span = values["span"]
        if span.line not in recipes:
            return fallback(**values)
        event, condition, required, effect, targets = recipes[span.line]
        gate = explicit_capabilities_gate(required, capability_registry=values["capability_registry"],
                                          capability_profile=values["capability_profile"])
        line = values["line"]
        blocked = (append_residual(values["residuals"], kind="dependency_contract", text=line, span=span,
                                   reason="Named-artifact echo requires its exact copy or committed public event",
                                   blockers=gate.blockers),) if gate.blockers else ()
        closure = gate.closure
        return OracleNode(node_id=values["node_id"], kind="triggered_ability", text=line, span=span,
            active_zone="battlefield", event=event, event_condition=condition, lowerable=True, exact=not blocked,
            template_id=f"named-artifact-echo-{event}-v1", effects=(effect,), target_schema=targets,
            runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE, COVERAGE, *source_entry_cast_runtime_coverage(condition)),
            mechanics=("cr-603-handling-triggered-abilities",), residual_ids=blocked,
            capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
            capability_profile=closure.profile if closure else None,
            capability_fingerprint=closure.fingerprint if closure else None)

    return compile_trigger
