from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from ..rules.capabilities import CapabilityRegistry
from .dependency_gate import dependency_gate, explicit_capabilities_gate
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .ir_model import OracleNode, OracleResidual, SourceSpan


DrawEffectTemplate = tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]



_DRAW_LIMIT = re.compile(
    r"^(?P<subject>players|each player|each opponent|your opponents|you) "
    r"can['’]t draw(?P<limit> more than one card each turn| cards?)\.?$",
    re.IGNORECASE,
)
_DRAW_DOUBLE = re.compile(
    r"^If you would draw a card, draw two cards instead\.?$",
    re.IGNORECASE,
)
_DRAW_REVEAL_FIRST = re.compile(
    r"^(?P<sentence>"
    r"(?:Reveal the first card you draw each turn|"
    r"Reveal the first card you draw on each of your turns|"
    r"You may reveal the first card you draw each turn as you draw it)"
    r"\.)(?:\s+(?P<remainder>.+))?$",
    re.IGNORECASE,
)
_DRAW_REVEAL_LINKED_DRAW = re.compile(
    r"^Whenever you reveal a (?P<quality>basic land|creature) card "
    r"this way, draw a card\.?$",
    re.IGNORECASE,
)
_TRIGGER_ABILITY_WORD = re.compile(
    r"^[A-Za-z][A-Za-z ']+\s+[—-]\s+"
    r"(?P<body>(?:when|whenever|at the beginning of)\b.+)$",
    re.IGNORECASE,
)


def trigger_ability_word_material_line(material_line: str) -> str:
    """Remove only a leading ability word that carries a triggered ability."""

    match = _TRIGGER_ABILITY_WORD.fullmatch(material_line)
    return match.group("body") if match is not None else material_line


def fixed_draw_effect_template(text: str) -> DrawEffectTemplate | None:
    """Lower closed mandatory and optional fixed-count draw instructions."""

    normalized = text.strip()
    if re.fullmatch(
        r"draw a card and reveal it\. if it isn['’]t a land card, "
        r"discard it\.?",
        normalized,
        re.IGNORECASE,
    ):
        return (
            "draw-reveal-discard-unless-land-controller-v1",
            (
                {
                    "op": "draw_with_actions",
                    "player": "$controller",
                    "count": 1,
                    "private": True,
                    "post_draw_actions": [
                        {"action": "reveal", "public": True},
                        {
                            "action": "discard_unless_type",
                            "card_type": "land",
                        },
                    ],
                },
            ),
            None,
            ("cr-121-drawing-a-card",),
        )
    match = re.fullmatch(
        rf"you may draw (?P<count>{FIXED_COUNT_PATTERN}) cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "optional-draw-controller-v1",
            (
                {
                    "op": "offer_draw",
                    "player": "$controller",
                    "drawer": "$controller",
                    "count": fixed_number(match.group("count")),
                    "private": True,
                },
            ),
            None,
            ("cr-121-drawing-a-card",),
        )
    match = re.fullmatch(
        rf"you may have target player draw (?P<count>{FIXED_COUNT_PATTERN}) cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "optional-draw-target-player-by-controller-v1",
            (
                {
                    "op": "offer_draw",
                    "player": "$controller",
                    "drawer": "$target.0",
                    "count": fixed_number(match.group("count")),
                    "private": True,
                },
            ),
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "any",
                "count": 1,
            },
            ("cr-121-drawing-a-card", "cr-115-targets"),
        )
    match = re.fullmatch(
        rf"(?:you )?draw (?P<count>{FIXED_COUNT_PATTERN}) cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "draw-controller-v1",
            (
                {
                    "op": "draw",
                    "player": "$controller",
                    "count": fixed_number(match.group("count")),
                    "private": True,
                },
            ),
            None,
            ("cr-121-drawing-a-card",),
        )
    match = re.fullmatch(
        rf"target (?P<relation>player|opponent) draws "
        rf"(?P<count>{FIXED_COUNT_PATTERN}) "
        r"cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        relation = match.group("relation").casefold()
        return (
            f"draw-target-{relation}-v1",
            (
                {
                    "op": "draw",
                    "player": "$target.0",
                    "count": fixed_number(match.group("count")),
                    "private": True,
                },
            ),
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": (
                    "opponent" if relation == "opponent" else "any"
                ),
                "count": 1,
            },
            ("cr-121-drawing-a-card", "cr-115-targets"),
        )
    match = re.fullmatch(
        rf"each player draws (?P<count>{FIXED_COUNT_PATTERN}) cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "draw-each-player-v1",
            (
                {
                    "op": "draw_each_player",
                    "count": fixed_number(match.group("count")),
                },
            ),
            None,
            (
                "cr-121-drawing-a-card",
            ),
        )
    return None


def static_draw_restriction_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower the closed fixed draw-prohibition and max-one wording family."""

    match = _DRAW_LIMIT.fullmatch(text)
    if match is None:
        return None
    subject = match.group("subject").casefold()
    relation = {
        "players": "any",
        "each player": "any",
        "each opponent": "opponent",
        "your opponents": "opponent",
        "you": "source_controller",
    }[subject]
    maximum = 1 if "more than one" in match.group("limit").casefold() else 0
    return (
        f"draw-maximum-{maximum}-{relation.replace('_', '-')}-static-v1",
        {
            "handler_id": "restriction.draw.maximum-per-turn.v1",
            "schema_version": 1,
            "event": "draw.permission",
            "condition": {"affected_player_relation": relation},
            "restriction": {"maximum_per_turn": maximum},
        },
        "zone.draw.library_to_hand",
    )


def static_draw_instruction_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower unconditional controller draw doubling at instruction scope."""

    if _DRAW_DOUBLE.fullmatch(text) is None:
        return None
    return (
        "draw-instruction-double-controller-static-v1",
        {
            "handler_id": "replacement.draw.instruction.multiply.v1",
            "schema_version": 1,
            "event": "draw.instruction",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"factor": 2},
        },
        "zone.draw.library_to_hand",
    )


def static_draw_result_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower draw-doubling wording to an individual result replacement."""

    if _DRAW_DOUBLE.fullmatch(text) is None:
        return None
    return (
        "draw-result-double-controller-static-v1",
        {
            "handler_id": "replacement.draw.result.multiply.v1",
            "schema_version": 1,
            "event": "draw",
            "condition": {
                "affected_player_relation": "source_controller",
            },
            "modification": {"factor": 2},
        },
        "zone.draw.result_generated_ordering",
    )


def draw_reveal_line_parts(text: str) -> tuple[str, str] | None:
    """Split only the closed CR 121.9 first-draw reveal grammar."""

    match = _DRAW_REVEAL_FIRST.fullmatch(text.strip())
    if match is None:
        return None
    return match.group("sentence"), str(match.group("remainder") or "")


def static_draw_reveal_handler(
    text: str,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Lower one first-draw reveal policy without interpreting its rider."""

    parts = draw_reveal_line_parts(text)
    if parts is None or parts[1]:
        return None
    sentence = parts[0].casefold()
    optional = sentence.startswith("you may")
    controller_turn = "on each of your turns" in sentence
    return (
        (
            "draw-reveal-first-controller-turn-static-v1"
            if controller_turn
            else "draw-reveal-first-controller-static-v1"
        ),
        {
            "handler_id": "action.draw.reveal-first.v1",
            "schema_version": 1,
            "event": "draw.reveal_as_drawn",
            "condition": {
                "affected_player_relation": "source_controller",
                "turn_relation": (
                    "source_controller_turn" if controller_turn else "any"
                ),
                "draw_ordinal": 1,
            },
            "reveal": {"optional": optional, "public": True},
        },
        "zone.draw.reveal_as_drawn",
    )


def linked_draw_reveal_condition(
    text: str,
) -> tuple[str, Mapping[str, Any]] | None:
    """Lower the two closed source-linked reveal-and-draw conditions."""

    match = _DRAW_REVEAL_LINKED_DRAW.fullmatch(text.strip())
    if match is None:
        return None
    quality = match.group("quality").casefold()
    conditions: list[Mapping[str, Any]] = [
        {
            "field": "reveal_source_object_id",
            "op": "eq",
            "value": "$source.object_id",
        }
    ]
    if quality == "basic land":
        conditions.extend(
            (
                {
                    "field": "revealed_card_types",
                    "op": "contains_any",
                    "value": ["land"],
                },
                {
                    "field": "revealed_card_supertypes",
                    "op": "contains_any",
                    "value": ["basic"],
                },
            )
        )
    else:
        conditions.append(
            {
                "field": "revealed_card_types",
                "op": "contains_any",
                "value": ["creature"],
            }
        )
    return quality.replace(" ", "-"), {"all": conditions}


def _draw_reveal_rider_node(
    *,
    node_id: str,
    remainder: str,
    span: SourceSpan,
    card_name: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    trigger_node: Callable[..., OracleNode | None],
    append_residual: Callable[..., str],
) -> OracleNode:
    linked = linked_draw_reveal_condition(remainder)
    if linked is None:
        unresolved = trigger_node(
            node_id=node_id,
            line=remainder,
            span=span,
            card_name=card_name,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
        if unresolved is not None:
            return unresolved
        residual_id = append_residual(
            residuals,
            kind="trigger",
            text=remainder,
            span=span,
            reason="draw reveal rider has no exact generic template",
            blockers=("source-linked draw reveal trigger grammar",),
        )
        return OracleNode(
            node_id=node_id,
            kind="triggered_ability",
            text=remainder,
            span=span,
            active_zone="battlefield",
            event="unresolved",
            lowerable=False,
            exact=False,
            residual_ids=(residual_id,),
        )

    quality, condition = linked
    trigger_mechanic = "cr-603-handling-triggered-abilities"
    effects = (
        {
            "op": "draw",
            "player": "$controller",
            "count": 1,
            "private": True,
        },
    )
    inferred_gate = dependency_gate(
        mechanics=(trigger_mechanic, "cr-121-drawing-a-card"),
        effects=effects,
        target_schema=None,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    gate = explicit_capabilities_gate(
        (*inferred_gate.capabilities, "zone.draw.reveal_as_drawn"),
        capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    blockers = tuple(
        dict.fromkeys(
            (
                *gate.blockers,
                *(
                    blocker
                    for blocker in inferred_gate.blockers
                    if blocker.startswith("mechanic:")
                ),
            )
        )
    )
    residual_ids = (
        (
            append_residual(
                residuals,
                kind="dependency_contract",
                text=remainder,
                span=span,
                reason=(
                    "source-linked draw reveal trigger depends on untrusted "
                    "rules capabilities"
                ),
                blockers=blockers,
            ),
        )
        if blockers
        else ()
    )
    closure = gate.closure
    return OracleNode(
        node_id=node_id,
        kind="triggered_ability",
        text=remainder,
        span=span,
        active_zone="battlefield",
        event="card.draw.revealed_by_source",
        lowerable=True,
        exact=not blockers,
        template_id=f"draw-after-reveal-{quality}-v1",
        effects=effects,
        event_condition=condition,
        mechanics=(trigger_mechanic, "cr-121-drawing-a-card"),
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
    )


def draw_reveal_nodes(
    *,
    node_id: str,
    line: str,
    span: SourceSpan,
    card_name: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    runtime_handler_node: Callable[..., OracleNode],
    trigger_node: Callable[..., OracleNode | None],
    append_residual: Callable[..., str],
) -> tuple[OracleNode, ...] | None:
    """Lower a closed CR 121.9 policy and its source-linked rider."""

    parts = draw_reveal_line_parts(line)
    if parts is None:
        return None
    reveal_text, remainder = parts
    compiled = static_draw_reveal_handler(reveal_text)
    if compiled is None:
        return None
    reveal_span = SourceSpan(
        start=span.start,
        end=span.start + len(reveal_text),
        line=span.line,
    )
    result = [
        runtime_handler_node(
            node_id=f"{node_id}:reveal",
            line=reveal_text,
            span=reveal_span,
            compiled=compiled,
            kind="static_ability",
            event="draw.reveal_as_drawn",
            dependency_reason=(
                "generic draw reveal depends on an untrusted rules capability"
            ),
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
        )
    ]
    if remainder:
        remainder_offset = line.find(remainder, len(reveal_text))
        if remainder_offset < 0:
            raise ValueError("Draw reveal rider is not source-spanned")
        result.append(
            _draw_reveal_rider_node(
                node_id=f"{node_id}:rider",
                remainder=remainder,
                span=SourceSpan(
                    start=span.start + remainder_offset,
                    end=span.start + remainder_offset + len(remainder),
                    line=span.line,
                ),
                card_name=card_name,
                trusted_mechanics=trusted_mechanics,
                capability_registry=capability_registry,
                capability_profile=capability_profile,
                residuals=residuals,
                trigger_node=trigger_node,
                append_residual=append_residual,
            )
        )
    return tuple(result)


def draw_reveal_or_trigger_nodes(
    *,
    permanent: bool,
    node_id: str,
    line: str,
    span: SourceSpan,
    card_name: str,
    trusted_mechanics: frozenset[str],
    capability_registry: CapabilityRegistry | None,
    capability_profile: str,
    residuals: list[OracleResidual],
    runtime_handler_node: Callable[..., OracleNode],
    trigger_node: Callable[..., OracleNode | None],
    append_residual: Callable[..., str],
) -> tuple[OracleNode, ...] | None:
    """Prefer the closed draw-reveal grammar, then ordinary triggers."""

    reveal_nodes = (
        draw_reveal_nodes(
            node_id=node_id,
            line=line,
            span=span,
            card_name=card_name,
            trusted_mechanics=trusted_mechanics,
            capability_registry=capability_registry,
            capability_profile=capability_profile,
            residuals=residuals,
            runtime_handler_node=runtime_handler_node,
            trigger_node=trigger_node,
            append_residual=append_residual,
        )
        if permanent
        else None
    )
    if reveal_nodes is not None:
        return reveal_nodes
    ordinary = trigger_node(
        node_id=node_id,
        line=line,
        span=span,
        card_name=card_name,
        trusted_mechanics=trusted_mechanics,
        capability_registry=capability_registry,
        capability_profile=capability_profile,
        residuals=residuals,
    )
    return (ordinary,) if ordinary is not None else None


__all__ = [
    "draw_reveal_nodes",
    "draw_reveal_or_trigger_nodes",
    "draw_reveal_line_parts",
    "fixed_draw_effect_template",
    "linked_draw_reveal_condition",
    "static_draw_instruction_handler",
    "static_draw_reveal_handler",
    "static_draw_result_handler",
    "static_draw_restriction_handler",
    "trigger_ability_word_material_line",
]
