from __future__ import annotations

import copy
from typing import Mapping, Sequence

from .model import DelayedTrigger, StackItem
from .turn_creature_cast_draw_model import (
    TURN_CREATURE_CAST_DRAW_FIELD,
    TURN_CREATURE_CAST_DRAW_LABEL,
    validate_turn_creature_cast_draw_effect,
    validate_turn_creature_cast_draw_trigger,
)


FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY = "trigger.delayed.fixed_upkeep_payment"
_FIXED_UPKEEP_PAYMENT_LABEL = "Pay at the beginning of your next upkeep"
_FIXED_UPKEEP_MANA_KEYS = frozenset({"GENERIC", "C", "W", "U", "B", "R", "G"})


def fixed_next_upkeep_payment_effect(cost: Mapping[str, int]) -> dict:
    """Seal one original-controller upkeep schedule and fixed ordinary mana payment."""
    if (not isinstance(cost, Mapping) or not cost or not set(cost) <= _FIXED_UPKEEP_MANA_KEYS
            or not set(cost) - {"GENERIC"}
            or any(type(amount) is not int or amount <= 0 for amount in cost.values())):
        raise ValueError("Delayed upkeep payment requires positive fixed ordinary mana")
    return {
        "op": "delayed_trigger",
        "controller": "$controller",
        "source": "$source",
        "label": _FIXED_UPKEEP_PAYMENT_LABEL,
        "event": "step.begin",
        "condition": {"player": "controller", "phase": "beginning", "step": "upkeep"},
        "stack": {
            "label": _FIXED_UPKEEP_PAYMENT_LABEL,
            "context": {"dynamic_effects": [{"op": "pay_or_lose", "cost": dict(cost)}]},
        },
        "once": True,
    }


def fixed_next_upkeep_payment_cost(effect) -> dict[str, int] | None:
    from .replacement.immutable import thaw_value

    try:
        literal = thaw_value(effect)
        payment, = literal["stack"]["context"]["dynamic_effects"]
        cost = payment["cost"]
        if literal != fixed_next_upkeep_payment_effect(cost):
            return None
        return dict(cost)
    except (TypeError, ValueError, KeyError):
        return None


NEXT_TURN_COUNTER_DRAW_FIELD = "next_turn_counter_draw_version"


def counter_next_turn_draw_effects(recipient: str) -> tuple[dict, dict]:
    def delayed(label, instruction):
        return {
            "op": "delayed_trigger",
            "controller": "$controller",
            "source": "$source",
            "label": label,
            "event": "step.begin",
            "condition": {"phase": "beginning", "step": "upkeep", "next_turn_after_sequence": "$turn_sequence"},
            "stack": {"label": label, "context": {"dynamic_effects": [instruction]}},
            "once": True,
        }

    return (
        delayed("May draw up to two cards", {"op": "offer_draw", NEXT_TURN_COUNTER_DRAW_FIELD: 1,
                                              "recipient": recipient, "private": True}),
        delayed("Draw a card", {"op": "draw", "count": 1, "private": True}),
    )


def materialize_delayed_trigger(
    trigger: DelayedTrigger,
    *,
    ref: str,
    stack_id: str,
    visibility: Sequence[str],
) -> StackItem:
    """Create the public stack incarnation of one waiting delayed trigger."""

    template = trigger.stack_template
    return StackItem(
        stack_id=stack_id,
        ref=ref,
        kind="triggered_ability",
        controller=trigger.controller,
        label=str(template.get("label") or trigger.label),
        source_object_id=trigger.source_object_id,
        semantic_key=template.get("semantic_key"),
        targets=list(template.get("targets") or []),
        notes=str(template.get("note") or ""),
        visibility=list(visibility),
        context={
            **copy.deepcopy(dict(template.get("context") or {})),
            "event": trigger.event_kind,
            **(
                {
                    "source_logical_object_id": (
                        trigger.source_logical_object_id
                    )
                }
                if trigger.source_logical_object_id is not None
                else {}
            ),
            "delayed_trigger_ref": trigger.ref,
        },
        referred_object_ids=list(trigger.referred_object_ids),
    )


def schedule_turn_creature_cast_draw(host, effect, *, actor):
    from .continuous_effect_state import ResolutionEffectSource
    from .rules.delayed_draw_capability_shapes import is_closed_turn_creature_cast_draw_program
    from .trigger_processing import schedule_delayed_trigger

    validate_turn_creature_cast_draw_effect(effect, actor=actor)
    source = ResolutionEffectSource.from_effect(effect)
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind not in {"spell", "spell_copy"} or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise ValueError("Same-turn creature-cast draw requires the resolving spell's controller")
    program = host.semantics.get(item.semantic_key)
    if program is None or not is_closed_turn_creature_cast_draw_program(program):
        raise ValueError("Same-turn creature-cast draw requires its original resolving program")
    return schedule_delayed_trigger(
        host, controller=actor, label=TURN_CREATURE_CAST_DRAW_LABEL, event_kind="spell.cast",
        condition={TURN_CREATURE_CAST_DRAW_FIELD: 1}, stack_template={},
        once=False, expires_turn_sequence=host.state.turn_sequence,
    ).ref


def turn_creature_cast_draw_matches(trigger, context):
    from .rules.spell_cast_events import SpellCastEvent

    validate_turn_creature_cast_draw_trigger(trigger)
    cast = SpellCastEvent.from_context(context)
    return cast.controller == trigger.controller and "creature" in cast.types


def turn_creature_cast_draw_occurrence_context(state, trigger, context):
    if (not trigger.active or trigger.expires_turn_sequence != state.turn_sequence or context is None
            or not turn_creature_cast_draw_matches(trigger, context)):
        raise ValueError("Same-turn creature-cast draw requires its current qualifying cast")
    return {"dynamic_effects": [{"op": "draw", "player": trigger.controller, "count": 1, "private": True}]}


__all__ = ["materialize_delayed_trigger"]
