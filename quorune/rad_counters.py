"""Sourceless rad trigger and the result of its committed Mill action."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .counter_state import CounterChange, commit_counter_changes, plan_counter_changes, player_counter_snapshot
from .errors import GameRuleError
from .model import StackItem
from .rad_counter_model import RAD_CAPABILITY, RAD_COUNTER, RAD_EFFECT_FIELD, RAD_TRIGGER_VERSION, RADIATION_CAUSE
from .replacement.immutable import FrozenMap
from .rules.committed_quantity_followups import CommittedQuantityFollowup


def _rad_count(state, seat: str) -> int:
    return player_counter_snapshot(state.players[seat]).get(RAD_COUNTER, 0)


def require_rad_authority(host) -> None:
    if host.state.rad_trigger_version is None:
        return  # Retain the historical passive counter for old checkpoints.
    from .rules.capabilities import load_default_capability_registry

    profile = host.state.config.review_profile
    if not load_default_capability_registry().closure((RAD_CAPABILITY,), profile=profile).trusted:
        raise GameRuleError("The inherent rad ability requires trusted gameplay authority")


def collect_rad_trigger(host, active: str, phase: str, step: str) -> tuple[StackItem, ...]:
    if (host.state.rad_trigger_version is None or (phase, step) != ("precombat_main", "main")
            or active not in host.active_seats or not _rad_count(host.state, active)):
        return ()
    require_rad_authority(host)
    ref = host._next_ref("S")
    return (StackItem(
        stack_id=host._stable_runtime_id("stack", ref),
        ref=ref,
        kind="triggered_ability",
        controller=active,
        label="Radiation",
        visibility=list(host.seats),
        context={
            "event": "step.begin", "phase": phase, "step": step, "player": active,
            RAD_EFFECT_FIELD: RAD_TRIGGER_VERSION,
            "dynamic_effects": [{"op": "mill", RAD_EFFECT_FIELD: RAD_TRIGGER_VERSION}],
        },
    ),)


def _require_rad_stack(host, item) -> str:
    controller = item.controller
    context = item.context
    if (
        host.state.rad_trigger_version != RAD_TRIGGER_VERSION or not host.state.stack
        or host.state.stack[-1] is not item or item.kind != "triggered_ability"
        or item.source_object_id is not None or item.card_object_id is not None or item.targets
        or item.semantic_key is not None or not context.get("currently_resolving")
        or type(context.get(RAD_EFFECT_FIELD)) is not int or context[RAD_EFFECT_FIELD] != RAD_TRIGGER_VERSION
        or (context.get("event"), context.get("phase"), context.get("step"), context.get("player"))
        != ("step.begin", "precombat_main", "main", controller)
    ):
        raise GameRuleError("Radiation requires its original resolving inherent ability")
    require_rad_authority(host)
    return controller


@dataclass(frozen=True, slots=True)
class RadMillFollowup:
    """The sole internal Mill follow-up for the inherent rad trigger."""

    def to_dict(self) -> dict[str, int]:
        return {RAD_EFFECT_FIELD: RAD_TRIGGER_VERSION}

    @classmethod
    def from_dict(cls, value) -> RadMillFollowup:
        if not isinstance(value, Mapping) or set(value) != {RAD_EFFECT_FIELD}:
            raise ValueError("Radiation Mill has a closed result descriptor")
        version = value[RAD_EFFECT_FIELD]
        if type(version) is not int or version != RAD_TRIGGER_VERSION:
            raise ValueError("Radiation Mill has an invalid result version")
        return cls()

    def from_committed_result(self, host, result) -> CommittedQuantityFollowup:
        from .milling import MillResult

        if not isinstance(result, MillResult):
            raise GameRuleError("Radiation requires the committed Mill result")
        count = sum(_milled_nonland(host, result.player, outcome) for outcome in result.cards)
        if not count:
            return CommittedQuantityFollowup(())
        return CommittedQuantityFollowup((FrozenMap({
            "op": "life", "player": result.player, "delta": -count,
            "cause": RADIATION_CAUSE, RAD_EFFECT_FIELD: RAD_TRIGGER_VERSION,
        }),))


def _milled_nonland(host, player: str, outcome) -> bool:
    from .zone_transition_model import PUBLIC_ZONES

    if outcome.destination not in PUBLIC_ZONES:
        return False
    card = host.state.cards.get(outcome.identity.object_id)
    if (card is None or card.zone != outcome.destination or card.owner != player
            or card.logical_object_id != outcome.destination_logical_object_id or not card.is_card_object):
        raise GameRuleError("Milled card changed its committed destination")
    if card.face_down:
        return False
    data = host._effective_card_data(card)
    return "land" not in host._type_parts(str(data.get("type_line") or ""))[0]


def is_rad_instruction(effect: Mapping) -> bool:
    return RAD_EFFECT_FIELD in effect or (
        isinstance(effect.get("followup"), Mapping) and RAD_EFFECT_FIELD in effect["followup"]
    )


def bind_rad_instruction(host, item, effect: Mapping) -> dict | None:
    controller = _require_rad_stack(host, item)
    if effect.get("op") == "life":
        validate_rad_consequence(host, effect, actor=controller, item=item)
        return dict(effect)
    count = _rad_count(host.state, controller)
    if set(effect) == {"op", RAD_EFFECT_FIELD} and effect.get("op") == "mill":
        RadMillFollowup.from_dict({RAD_EFFECT_FIELD: effect[RAD_EFFECT_FIELD]})
        if not count or controller not in host.active_seats:
            return None
        return {"op": "mill", "player": controller, "count": count,
                "reason": RADIATION_CAUSE, "followup": RadMillFollowup().to_dict()}
    expected = {"op": "mill", "player": controller, "count": count,
                "reason": RADIATION_CAUSE, "followup": RadMillFollowup().to_dict()}
    selection_key = "_replacement_selections"
    shape = {key: value for key, value in effect.items() if key != selection_key}
    if (selection_key not in effect or shape != expected or type(effect.get("count")) is not int
            or not count or controller not in host.active_seats):
        raise GameRuleError("Radiation Mill changed before replacement resume")
    RadMillFollowup.from_dict(effect["followup"])
    return dict(effect)


def validate_rad_consequence(host, effect: Mapping, *, actor: str, item=None) -> int:
    if item is None:
        source = effect.get("_runtime_source")
        if not isinstance(source, Mapping) or not host.state.stack or source.get("stack_ref") != host.state.stack[-1].ref:
            raise GameRuleError("Radiation consequence is missing its resolving stack source")
        item = host.state.stack[-1]
    controller = _require_rad_stack(host, item)
    fields = {"op", "player", "delta", "cause", RAD_EFFECT_FIELD}
    permitted = {"_replacement_selections", "_runtime_source"}
    delta = effect.get("delta")
    if (set(effect) - permitted != fields or effect["op"] != "life" or effect["player"] != controller
            or actor != controller or controller not in host.active_seats or effect["cause"] != RADIATION_CAUSE
            or type(delta) is not int or delta >= 0
            or type(effect[RAD_EFFECT_FIELD]) is not int or effect[RAD_EFFECT_FIELD] != RAD_TRIGGER_VERSION):
        raise GameRuleError("Radiation has a closed committed life and counter result")
    return -delta


def plan_rad_removal(host, effect: Mapping, *, actor: str):
    amount = validate_rad_consequence(host, effect, actor=actor)
    return plan_counter_changes(host, (CounterChange("player", actor, RAD_COUNTER, -amount),))


def commit_rad_removal(host, plan) -> None:
    (transition,) = commit_counter_changes(host, plan)
    host._log(
        transition.subject_id, "player.counter",
        f"{transition.subject_id} removed {-transition.applied_delta} rad counter(s).",
        {"player": transition.subject_id, "counter": RAD_COUNTER, "delta": transition.applied_delta,
         "before": transition.before, "after": transition.after, "reason": RADIATION_CAUSE},
        changed_players=plan.changed_players,
    )
