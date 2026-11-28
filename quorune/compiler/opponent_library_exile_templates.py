from __future__ import annotations

"""Compile the printed results for committed opponent-library exiles."""

from collections.abc import Mapping
from typing import Any


EACH_OPPONENT_TOP_EXILE_TEMPLATE_ID = "each-opponent-library-top-exile-v1"
END_STEP_OPPONENT_EXILE_TEMPLATE_ID = "end-step-opponent-library-exile-play-v1"
EACH_OPPONENT_TOP_EXILE_MECHANIC = "fixed-each-opponent-library-top-exile"
OPPONENT_LIBRARY_EXILE_PLAY_TEMPLATE_ID = "controlled-opponent-library-exile-play-v1"
OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC = "fixed-controlled-opponent-library-exile-play"
OPPONENT_LIBRARY_EXILE_PLAY_VARIANT = "controlled_opponent_library_exile_batch"

ATTACK_EXILE_HEADER = "whenever an opponent attacks you and/or one or more planeswalkers you control"
ATTACK_EXILE_BODY = (
    "exile the top card of that player's library. you may play that card for as long as it remains exiled, "
    "and you may spend mana as though it were mana of any color to cast it."
)

_SELF_ATTACK_CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
_EFFECT_CONTROLLER_CONDITION = {
    "field": "effect_controller",
    "op": "eq",
    "value": "$source.controller",
}
_EACH_OPPONENT_TOP_EXILE_BODY = "each opponent exiles the top card of their library."
_END_STEP_OPPONENT_EXILE_BODY = (
    "exile the top card of each opponent's library. you may play those cards for as long as they remain exiled. "
    "if you cast a spell this way, you may spend mana as though it were mana of any color to cast it."
)
_CONTROLLED_OPPONENT_LIBRARY_PLAY_BODY = (
    "you may play those cards until the end of your next turn. "
    "mana of any type can be spent to cast spells this way."
)


def _event_value(binding: Any) -> str | None:
    event = getattr(binding, "event", None)
    return getattr(event, "value", event) if event is not None else None


def _active_zone(binding: Any) -> str | None:
    return getattr(binding, "active_zone", getattr(binding, "public_active_zone", None))


def _normalized_body(body: str) -> str:
    return " ".join(body.strip().split()).casefold()


def draw_then_opponent_top_exile_template(text: str):
    from ..rules.opponent_library_exile_triggers import (
        DRAW_THEN_OPPONENT_EXILE_MECHANIC, draw_then_opponent_top_exile_effects,
    )

    if _normalized_body(text) != "draw a card. each opponent exiles the top card of their library.":
        return None
    return ("draw-then-each-opponent-top-exile-v1", draw_then_opponent_top_exile_effects(),
            None, ("cr-121-drawing-a-card", DRAW_THEN_OPPONENT_EXILE_MECHANIC))


def draw_then_opponent_exile_activation_gate(
    *, effects, active_zone, cost_schema, target_schema, mechanics, capability_registry, capability_profile,
):
    from ..rules.opponent_library_exile_triggers import (
        DRAW_THEN_OPPONENT_EXILE_CAPABILITY, DRAW_THEN_OPPONENT_EXILE_MECHANIC, library_exile_trigger_node_capabilities,
    )
    from .dependency_gate import DependencyGate, explicit_capabilities_gate

    if DRAW_THEN_OPPONENT_EXILE_MECHANIC not in mechanics:
        return None
    required = library_exile_trigger_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanics, cost_schema=cost_schema,
    )
    if not required or active_zone != "battlefield":
        return DependencyGate(blockers=(f"capability:{DRAW_THEN_OPPONENT_EXILE_CAPABILITY}",))
    return explicit_capabilities_gate(required, capability_registry=capability_registry,
                                      capability_profile=capability_profile)


def each_opponent_library_top_exile_result(
    binding: Any,
    body: str,
) -> tuple[str, tuple[Mapping[str, Any], ...], None, tuple[str, ...]] | None:
    """Compile the source attack or controller end step through the same move."""
    from ..controller_creature_keyword_absence import is_controller_end_step_schedule
    from ..opponent_library_exile_model import (
        DAMAGED_PLAYER_PRIVATE_TOP_EVENT, DAMAGED_PLAYER_PRIVATE_TOP_VARIANT,
        damaged_player_private_top_condition, damaged_player_private_top_effect, each_opponent_top_exile_effect,
    )

    event = _event_value(binding)
    condition = getattr(binding, "event_condition", None)
    if _active_zone(binding) != "battlefield" or body != getattr(binding, "body", None):
        return None
    printed = _normalized_body(body).replace("’", "'")
    if (event == DAMAGED_PLAYER_PRIVATE_TOP_EVENT
            and condition == damaged_player_private_top_condition()
            and getattr(binding, "variant", None) == DAMAGED_PLAYER_PRIVATE_TOP_VARIANT
            and printed == (
                "look at the top card of their library, then exile it face down. "
                "for as long as it remains exiled, you may play it, and mana of any type can be spent to cast that spell."
            )):
        return ("equipped-damage-player-top-private-play-v1", (damaged_player_private_top_effect(),), None,
                (EACH_OPPONENT_TOP_EXILE_MECHANIC,))
    from ..mutate_model import MUTATE_EVENT

    private_body = ("exile the top card of each opponent's library face down. you may look at "
                    "and play those cards for as long as they remain exiled.")
    if event == MUTATE_EVENT + ".self" and condition is None and printed == private_body:
        from ..opponent_library_exile_model import private_top_exile_effect

        return ("mutate-private-opponent-library-top-exile-v1", (private_top_exile_effect(),), None,
                (EACH_OPPONENT_TOP_EXILE_MECHANIC,))
    from ..rules.attacking_opponent_library_exile import (
        ATTACKED_PLAYER_OR_WALKER_EVENT, ATTACKED_PLAYER_OR_WALKER_VARIANT,
        attack_top_exile_condition,
    )

    if (event == ATTACKED_PLAYER_OR_WALKER_EVENT and condition == attack_top_exile_condition()
            and getattr(binding, "variant", None) == ATTACKED_PLAYER_OR_WALKER_VARIANT
            and printed == ATTACK_EXILE_BODY):
        from ..opponent_library_exile_model import attacking_opponent_top_exile_effect

        return ("attacking-opponent-top-exile-play-v1", (attacking_opponent_top_exile_effect(),), None,
                (EACH_OPPONENT_TOP_EXILE_MECHANIC,))
    if event == "step.begin" and is_controller_end_step_schedule(condition) and printed == _END_STEP_OPPONENT_EXILE_BODY:
        return (
            END_STEP_OPPONENT_EXILE_TEMPLATE_ID,
            (each_opponent_top_exile_effect(grant_incarnation_play=True),),
            None,
            (EACH_OPPONENT_TOP_EXILE_MECHANIC,),
        )
    if (
        event != "creature.attacks"
        or getattr(binding, "variant", None) != "source_attacks"
        or condition != _SELF_ATTACK_CONDITION
        or printed != _EACH_OPPONENT_TOP_EXILE_BODY
    ):
        return None

    return (
        EACH_OPPONENT_TOP_EXILE_TEMPLATE_ID,
        (each_opponent_top_exile_effect(),),
        None,
        (EACH_OPPONENT_TOP_EXILE_MECHANIC,),
    )


def controlled_opponent_library_exile_play_result(
    binding: Any,
    body: str,
) -> tuple[str, tuple[Mapping[str, Any], ...], None, tuple[str, ...]] | None:
    """Compile the effect-controller-filtered occurrence into play access."""

    from ..opponent_library_exile_model import OPPONENT_LIBRARY_EXILE_EVENT

    if (
        _event_value(binding) != OPPONENT_LIBRARY_EXILE_EVENT
        or getattr(binding, "variant", None) != OPPONENT_LIBRARY_EXILE_PLAY_VARIANT
        or _active_zone(binding) != "battlefield"
        or getattr(binding, "event_condition", None) != _EFFECT_CONTROLLER_CONDITION
        or body != getattr(binding, "body", None)
        or _normalized_body(body) != _CONTROLLED_OPPONENT_LIBRARY_PLAY_BODY
    ):
        return None
    from ..opponent_library_exile_model import opponent_library_exile_play_effect

    return (
        OPPONENT_LIBRARY_EXILE_PLAY_TEMPLATE_ID,
        (opponent_library_exile_play_effect(),),
        None,
        (OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC,),
    )


__all__ = [
    "EACH_OPPONENT_TOP_EXILE_MECHANIC",
    "EACH_OPPONENT_TOP_EXILE_TEMPLATE_ID",
    "OPPONENT_LIBRARY_EXILE_PLAY_MECHANIC",
    "OPPONENT_LIBRARY_EXILE_PLAY_TEMPLATE_ID",
    "OPPONENT_LIBRARY_EXILE_PLAY_VARIANT",
    "controlled_opponent_library_exile_play_result",
    "each_opponent_library_top_exile_result",
]
