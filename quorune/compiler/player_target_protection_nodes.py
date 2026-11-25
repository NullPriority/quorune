from __future__ import annotations

"""Source-spanned fixed player Hexproof and Shroud productions."""

from ..semantic_runtime.player_target_protection import (
    CONTROLLER_TURN_PLAYER_HEXPROOF_HANDLER_ID,
    PLAYER_TARGET_PROTECTION_CAPABILITY_ID,
    PLAYER_TARGET_PROTECTION_HANDLER_ID,
)
from .continuous_templates import fixed_public_state_characteristics_handler, fixed_query_keyword_grant_handler
from .static_runtime_nodes import runtime_handler_node


PLAYER_HUMAN_HEXPROOF_TEMPLATE = "controller-player-human-hexproof-v1"
TURN_PLAYER_AND_SOURCE_HEXPROOF_TEMPLATE = "controller-turn-player-and-source-hexproof-v1"
PLAYER_PLANESWALKER_CREATURE_HEXPROOF_TEMPLATE = "controller-player-planeswalker-creature-hexproof-v1"
_COMPOUND_HEXPROOF = {
    "you and humans you control have hexproof": (
        PLAYER_HUMAN_HEXPROOF_TEMPLATE, ("Humans you control",),
    ),
    "you, planeswalkers you control, and other creatures you control have hexproof": (
        PLAYER_PLANESWALKER_CREATURE_HEXPROOF_TEMPLATE,
        ("Planeswalkers you control", "Other creatures you control"),
    ),
}


def _player_protection_descriptor(keyword):
    return {
        "handler_id": PLAYER_TARGET_PROTECTION_HANDLER_ID,
        "schema_version": 1,
        "event": "characteristics.evaluate",
        "keyword": keyword,
    }


def _turn_player_and_source_hexproof(source_name):
    self_grant = fixed_public_state_characteristics_handler(
        "During your turn, this creature has hexproof.", source_name=source_name,
    )
    if self_grant is None:
        return None
    _, source, capabilities = self_grant
    player = {
        **_player_protection_descriptor("hexproof"),
        "handler_id": CONTROLLER_TURN_PLAYER_HEXPROOF_HANDLER_ID,
        "source_condition": source["source_condition"],
    }
    return (
        TURN_PLAYER_AND_SOURCE_HEXPROOF_TEMPLATE, (player, source),
        tuple(sorted({PLAYER_TARGET_PROTECTION_CAPABILITY_ID, *capabilities})),
    )


def _player_protection_components(normalized, *, source_name):
    if normalized == "during your turn, you and this creature have hexproof":
        return _turn_player_and_source_hexproof(source_name)
    if normalized in {"you have hexproof", "you have shroud"}:
        return (
            "controller-player-target-protection-v1",
            _player_protection_descriptor(normalized.rsplit(" ", 1)[1]),
            PLAYER_TARGET_PROTECTION_CAPABILITY_ID,
        )
    if normalized not in _COMPOUND_HEXPROOF:
        return None
    template, subjects = _COMPOUND_HEXPROOF[normalized]
    grants = tuple(fixed_query_keyword_grant_handler(subject + " have hexproof.") for subject in subjects)
    if any(grant is None for grant in grants):
        return None
    return (
        template,
        (_player_protection_descriptor("hexproof"), *(grant[1] for grant in grants)),
        tuple(sorted({PLAYER_TARGET_PROTECTION_CAPABILITY_ID, *(cap for grant in grants for cap in grant[2])})),
    )


def player_target_protection_node(
    *, node_id, line, material_line, span, source_name, capability_registry, capability_profile, residuals,
):
    compiled = _player_protection_components(material_line.casefold().rstrip("."), source_name=source_name)
    if compiled is None:
        return None
    return runtime_handler_node(
        node_id=node_id, line=line, span=span, compiled=compiled,
        kind="static_ability", event="characteristics.evaluate",
        dependency_reason="Player protection requires current trusted source components and shared target legality.",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
