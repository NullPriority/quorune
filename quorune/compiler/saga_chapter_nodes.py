from __future__ import annotations

"""Typed ordinary Saga chapter programs over the existing lore owner."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ..read_ahead import saga_chapter_line
from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import dependency_gate
from .ir_model import OracleNode, OracleResidual, SourceSpan, append_residual


SAGA_CHAPTER_TRIGGER_MECHANIC = "trigger-event-saga-chapter"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[..., CompiledEffectTemplate]


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
) -> tuple[OracleNode, ...] | None:
    """Lower one ordinary printed chapter into one node per event threshold."""

    parsed = saga_chapter_line(material_line)
    if parsed is None:
        return None
    printed_saga = "saga" in {value.casefold() for value in printed_subtypes}
    declared = tuple(declared_chapters)
    chapter_set_closed = bool(declared) and set(parsed.chapters).issubset(declared)
    template, effects, target_schema, mechanics = effect_template(
        parsed.body,
        card_name=card_name,
    )
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
    return tuple(
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
                "saga_chapter_dispatch",
                "apnap_trigger_placement",
            ),
        )
        for index, chapter in enumerate(parsed.chapters)
    )


__all__ = [
    "SAGA_CHAPTER_TRIGGER_MECHANIC",
    "ordinary_saga_chapter_nodes",
]
