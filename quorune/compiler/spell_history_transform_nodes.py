from __future__ import annotations

"""Closed Oracle lowering for legacy upkeep self-transform triggers."""

import re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.capabilities import CapabilityRegistry
from ..rules.source_references import SourceReferenceSpec
from ..spell_history_transform_model import (
    PreviousTurnSpellCondition,
    SPELL_HISTORY_TRANSFORM_CAPABILITY_ID,
    SPELL_HISTORY_TRANSFORM_COVERAGE,
    SPELL_HISTORY_TRANSFORM_MECHANIC_ID,
    SpellHistoryTransformSpec,
)
from .dependency_gate import explicit_capability_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


def previous_turn_transform_trigger_node(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    card_name: str,
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
) -> OracleNode | None:
    """Lower exactly the two historical Innistrad spell-count wordings."""

    source_name = SourceReferenceSpec(card_name).regex_pattern
    match = re.fullmatch(
        rf"At the beginning of each upkeep, if (?P<condition>"
        rf"no spells were cast last turn|"
        rf"a player cast two or more spells last turn), "
        rf"transform (?P<source>this (?:creature|permanent)|{source_name})\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    condition = (
        PreviousTurnSpellCondition.NO_SPELLS
        if match.group("condition").casefold().startswith("no spells")
        else PreviousTurnSpellCondition.ONE_PLAYER_TWO_OR_MORE
    )
    spec = SpellHistoryTransformSpec(condition=condition)
    gate = explicit_capability_gate(
        SPELL_HISTORY_TRANSFORM_CAPABILITY_ID,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason=(
                    "Previous-turn self-transform requires its typed history "
                    "and transform owners"
                ),
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=line,
        span=span,
        active_zone="battlefield",
        event="step.begin",
        lowerable=True,
        exact=not residual_ids,
        template_id=spec.template_id,
        effects=(spec.effect(),),
        event_condition=spec.event_condition(),
        runtime_coverage=(
            CURRENT_ABILITY_FRAGMENT_COVERAGE,
            "intervening_condition",
            SPELL_HISTORY_TRANSFORM_COVERAGE,
        ),
        mechanics=(
            "cr-603-handling-triggered-abilities",
            SPELL_HISTORY_TRANSFORM_MECHANIC_ID,
        ),
        residual_ids=residual_ids,
        capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=(
            closure.fingerprint if closure is not None else None
        ),
    )


__all__ = ["previous_turn_transform_trigger_node"]
