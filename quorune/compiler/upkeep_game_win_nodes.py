"""Compile current controller upkeep wins with a represented condition."""

from . import regex_working_set as re
from ..continuous_conditions import FIXED_PUBLIC_STATE_INTERVENING_COVERAGE, FixedPublicStateConditionKind
from ..rules.upkeep_game_win import (
    UPKEEP_GAME_WIN_CAPABILITIES, UPKEEP_GAME_WIN_TEMPLATE, creature_count_win_condition,
    exact_controller_quantity_win_condition,
    upkeep_game_win_condition, upkeep_game_win_effect,
)
from .dependency_gate import explicit_capabilities_gate
from .fixed_numbers import fixed_number
from .ir_model import OracleNode, append_residual
from .public_state_queries import fixed_public_state_condition


_WIN = re.compile(r"At the beginning of your upkeep, if (?P<condition>.+?), you win the game\.?$", re.I)
_CREATURE_COUNT = re.compile(r"you control (?P<count>twenty|[1-9][0-9]*) or more creatures", re.I)
_GRAVE_COUNT = re.compile(
    r"(?P<count>twenty|[1-9][0-9]*) or more creature cards are in your graveyard", re.I,
)
_EXACT_QUANTITY = re.compile(
    r"you have exactly (?P<count>one|thirteen|[1-9][0-9]*) (?P<resource>life|cards in your hand)", re.I,
)


def _current_win_condition(text, card_name):
    if match := _CREATURE_COUNT.fullmatch(text):
        return creature_count_win_condition("battlefield", fixed_number(match["count"]))
    if match := _GRAVE_COUNT.fullmatch(text):
        return creature_count_win_condition("graveyard", fixed_number(match["count"]))
    if match := _EXACT_QUANTITY.fullmatch(text):
        count = 13 if match["count"].casefold() == "thirteen" else fixed_number(match["count"])
        return exact_controller_quantity_win_condition(match["resource"].casefold(), count)
    condition = fixed_public_state_condition(text, source_name=card_name)
    if condition is not None and condition.kind is FixedPublicStateConditionKind.CONTROLLER_LIFE_AT_LEAST and condition.amount:
        return condition
    return None


def upkeep_game_win_node(*, node_id, line, material_line, span, card_name,
                         capability_registry, capability_profile, residuals):
    match = _WIN.fullmatch(material_line)
    if match is None or (condition := _current_win_condition(match["condition"], card_name)) is None:
        return None
    gate = explicit_capabilities_gate(
        UPKEEP_GAME_WIN_CAPABILITIES,
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual_ids = ()
    if gate.blockers:
        residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
                                        reason="The upkeep win requires its live intervening condition and outcome owner.",
                                        blockers=gate.blockers),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event="step.begin", lowerable=True, exact=not gate.blockers,
        template_id=UPKEEP_GAME_WIN_TEMPLATE, event_condition=upkeep_game_win_condition(condition),
        effects=(upkeep_game_win_effect(),),
        runtime_coverage=("intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE),
        residual_ids=residual_ids, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
