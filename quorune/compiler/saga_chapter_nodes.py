from __future__ import annotations

"""Typed ordinary Saga chapter programs over the existing lore owner."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..read_ahead import saga_chapter_line
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual
from .saga_printed_cost_search import printed_mana_cost_artifact_search_template
from .source_tap_duration import controlled_saga_untap_restraint_template
from .phasing_nodes import controlled_saga_phase_out_template


def _goad_and_hand_count_chapter(parsed):
    from ..goad_instruction import (
        HAND_COUNT_UNBLOCKABLE_MECHANIC, WATCHED_COMBAT_DRAW_MECHANIC,
        hand_count_unblockable_effect, hand_count_unblockable_schema,
        watched_combat_draw_effect, watched_combat_draw_schema,
    )

    body = parsed.body.casefold()
    if parsed.chapters == (1, 2) and body == (
        "for each opponent, tap up to one target creature that player controls. goad those creatures. "
        "until your next turn, whenever any of those creatures deals combat damage to a player, draw a card."
    ):
        return ("saga-goad-watched-combat-draw-v1", (watched_combat_draw_effect(),),
                watched_combat_draw_schema(), (WATCHED_COMBAT_DRAW_MECHANIC,))
    if parsed.chapters == (3,) and body == (
        "target creature you control gets +1/+1 until end of turn for each card in your hand and can't be blocked this turn."
    ):
        return ("saga-hand-count-and-unblockable-v1", (hand_count_unblockable_effect(),),
                hand_count_unblockable_schema(), (HAND_COUNT_UNBLOCKABLE_MECHANIC,))
    return None


SAGA_CHAPTER_TRIGGER_MECHANIC = "trigger-event-saga-chapter"

def _source_controlled_block_chapter(body):
    if body.casefold() != (
        "up to one target creature can't block for as long as you control this saga. the ring tempts you."
    ):
        return None
    from ..ring_model import RING_MECHANIC
    from ..rules.temporary_declaration_restrictions import (
        SOURCE_CONTROLLED_BLOCK_MECHANIC, source_controlled_block_effects, source_controlled_block_target_schema,
    )

    return ("source-controlled-block-ring-v1", source_controlled_block_effects(),
            source_controlled_block_target_schema(), (SOURCE_CONTROLLED_BLOCK_MECHANIC, RING_MECHANIC))


CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[..., CompiledEffectTemplate]


def _announced_three_counter_chapter(body):
    if " ".join(body.casefold().split()) != (
        "distribute three +1/+1 counters among one, two, or three target creatures you control."
    ):
        return None
    from ..announced_counter_allocation import (
        ANNOUNCED_COUNTER_MECHANIC, announced_counter_effect, announced_counter_target_schema,
    )
    from ..rules.counter_target_set_shapes import QUANTIFIED_COUNTER_MECHANIC

    return (
        "announced-three-counter-targets-v1", (announced_counter_effect(),), announced_counter_target_schema(),
        ("cr-122-counters", "cr-115-targets", QUANTIFIED_COUNTER_MECHANIC, ANNOUNCED_COUNTER_MECHANIC),
    )


def _same_turn_noncreature_keyword(body):
    from ..event_spell_copy_model import SAME_TURN_CAST_KEYWORD_MECHANIC, same_turn_cast_keyword_effect

    if body.casefold() != (
        "whenever you cast a noncreature spell this turn, target creature you control gains double strike until end of turn."
    ):
        return None
    return "same-turn-noncreature-cast-keyword-v1", (same_turn_cast_keyword_effect(),), None, (
        SAME_TURN_CAST_KEYWORD_MECHANIC,
    )


def ordinary_saga_chapter_nodes(
    *,
    node_id: str,
    line: str,
    material_line: str,
    span: SourceSpan,
    card_name: str,
    printed_subtypes: Sequence[str],
    declared_chapters: Sequence[int],
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    effect_template: EffectCompiler,
    grant: tuple[CompiledEffectTemplate, tuple[OracleNode, ...], tuple[str, ...]] | None = None,
) -> tuple[OracleNode, ...] | None:
    """Lower one ordinary printed chapter into one node per event threshold."""

    parsed = saga_chapter_line(material_line)
    if parsed is None:
        return None
    printed_saga = "saga" in {value.casefold() for value in printed_subtypes}
    declared = tuple(declared_chapters)
    chapter_set_closed = bool(declared) and set(parsed.chapters).issubset(declared)
    prepared = _same_turn_noncreature_keyword(parsed.body) if printed_saga and parsed.chapters == (3,) else None
    children = ()
    grant_dependencies = ()
    if prepared is None and printed_saga and parsed.chapters == (1,):
        prepared = _source_controlled_block_chapter(parsed.body)
    if grant is not None and printed_saga and chapter_set_closed:
        prepared, children, grant_dependencies = grant
    if prepared is None and printed_saga and parsed.chapters == (1, 2):
        from .conditional_exile_templates import saga_chapter_per_player_nonsaga_exile

        prepared = (
            controlled_saga_untap_restraint_template(parsed.body)
            or controlled_saga_phase_out_template(parsed.body)
            or saga_chapter_per_player_nonsaga_exile(parsed.body)
        )
    if prepared is None and printed_saga and chapter_set_closed:
        prepared = (printed_mana_cost_artifact_search_template(parsed.body)
                    or _announced_three_counter_chapter(parsed.body))
    template, effects, target_schema, mechanics = prepared or effect_template(
        parsed.body,
        card_name=card_name,
    )
    if prepared is None and printed_saga and chapter_set_closed:
        bounded = _goad_and_hand_count_chapter(parsed)
        if bounded is not None:
            template, effects, target_schema, mechanics = bounded
    recognized = printed_saga and chapter_set_closed and template is not None
    dependencies = (
        "cr-603-handling-triggered-abilities",
        SAGA_CHAPTER_TRIGGER_MECHANIC,
        *mechanics,
    )
    gate = dependency_gate(
        mechanics=dependencies if recognized else (),
        effects=effects if recognized else (),
        target_schema=target_schema if recognized else None,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        explicit_capabilities=grant_dependencies,
    )
    if not recognized:
        blocker = (
            "ordinary Saga chapter requires a printed Saga with contiguous "
            "declared chapter symbols and an independently exact body"
        )
        residual_id = append_residual(
            residuals,
            kind="saga_chapter",
            text=line,
            span=span,
            reason=blocker,
            blockers=(
                "ordinary Saga chapter event binding",
                "exact typed chapter effect body",
            ),
        )
        return (
            OracleNode(
                node_id=node_id,
                kind="triggered_ability",
                text=line,
                span=span,
                active_zone="battlefield",
                event="unresolved",
                lowerable=False,
                exact=False,
                residual_ids=(residual_id,),
            ),
        )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=line,
                span=span,
                reason="Saga chapter depends on untrusted typed capabilities",
                blockers=gate.blockers,
            ),
        )
        if gate.blockers
        else ()
    )
    closure = gate.closure
    triggers = tuple(
        OracleNode(
            node_id=(
                node_id
                if index == 0
                else f"{node_id}:chapter:{chapter}"
            ),
            kind="triggered_ability",
            text=line,
            span=span,
            active_zone="battlefield",
            event=f"saga.chapter.{chapter}",
            lowerable=True,
            exact=not gate.blockers,
            template_id=f"ordinary-saga-chapter-{chapter}-v1",
            effects=effects,
            target_schema=target_schema,
            mechanics=dependencies,
            residual_ids=residual_ids,
            capability_dependencies=gate.capabilities,
            capability_closure=(
                closure.reachable if closure is not None else ()
            ),
            capability_profile=(
                closure.profile if closure is not None else None
            ),
            capability_fingerprint=(
                closure.fingerprint if closure is not None else None
            ),
            runtime_coverage=(
                CURRENT_ABILITY_FRAGMENT_COVERAGE,
                "saga_chapter_dispatch",
                "apnap_trigger_placement",
            ),
        )
        for index, chapter in enumerate(parsed.chapters)
    )
    return (*triggers, *children)


__all__ = [
    "SAGA_CHAPTER_TRIGGER_MECHANIC",
    "ordinary_saga_chapter_nodes",
]
