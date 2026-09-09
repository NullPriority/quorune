from __future__ import annotations

"""Typed fixed Suspend special action and exile trigger lifecycle."""

from dataclasses import replace
from typing import Any, Mapping, Protocol, Sequence

from .cast_lifecycles import (
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
    SUSPEND_HASTE_CONTEXT_FIELD,
)
from .cast_timing import cast_timing_is_legal
from .compiled_cast_lifecycles import compiled_fixed_cast_lifecycle_specs
from .compiled_cast_timing import compiled_cast_timing_permissions
from .counter_placement import (
    commit_prepared_counter_placements,
    CounterPlacementError,
    CounterPlacementRequest,
    prepare_counter_placements,
)
from .counter_removal import (
    commit_counter_removal_effect,
    CounterRemoval,
    CounterRemovalError,
    plan_counter_removal_effect,
)
from .errors import GameRuleError, StateInvariantError
from .mana_undo import clear_mana_undo_stack
from .model import CardInstance, StackItem
from .rules.action_proposals import ActionOffer, freeze_json
from .stack_resolution import complete_stack_resolution
from .trigger_processing import enqueue_trigger_batch


SUSPEND_UPKEEP_SEMANTIC_KEY = "builtin:suspend-upkeep-counter"
SUSPEND_CAST_SEMANTIC_KEY = "builtin:suspend-cast-choice"
SUSPEND_EXILE_CAST_PRODUCER = "suspend"


class SuspendHost(Protocol):
    state: Any
    semantics: Any
    seats: Sequence[str]
    active_seats: Sequence[str]
    turn_priority: Any

    def _check_priority(self, seat: str) -> None: ...

    def _resolve_object(
        self,
        actor: str,
        ref: str,
        *,
        zones: set[str],
        owned_only: bool = False,
    ) -> CardInstance: ...

    def card_record(self, card: CardInstance) -> Any: ...

    def _cost_is_affordable(
        self,
        seat: str,
        requirements: Mapping[str, int],
        *,
        spend_context: Any = None,
    ) -> bool: ...

    def _pay_for_cost(
        self,
        seat: str,
        requirements: Mapping[str, int],
        response: Mapping[str, Any],
        *,
        spend_context: Any = None,
    ) -> tuple[dict[str, int], list[dict[str, Any]]]: ...

    def move_card(self, object_id: str, destination: str, **kwargs: Any) -> Any: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    def _next_ref(self, prefix: str) -> str: ...

    def _stable_runtime_id(self, kind: str, ref: str) -> str: ...

    def _stabilize(self) -> bool: ...

    def _grant_priority(self, seat: str | None) -> None: ...


def _suspend_spec(
    host: SuspendHost,
    card: CardInstance,
) -> FixedCastLifecycleSpec | None:
    values = tuple(
        spec
        for spec in compiled_fixed_cast_lifecycle_specs(host, card)
        if spec.kind is FixedCastLifecycleKind.SUSPEND
    )
    return values[0] if len(values) == 1 else None


def _suspend_timing_is_legal(
    host: SuspendHost,
    seat: str,
    card: CardInstance,
) -> bool:
    record = host.card_record(card)
    if record is None:
        return False
    face = record.faces[0] if record.faces else None
    type_line = str(face.get("type_line") or "") if face else record.type_line
    face_name = str(face.get("name") or "") if face else None
    return cast_timing_is_legal(
        host.state,
        seat,
        type_line,
        compiled_cast_timing_permissions(host, card, face_name=face_name),
    )


def build_suspend_offer(
    host: SuspendHost,
    seat: str,
    card: CardInstance,
) -> ActionOffer | None:
    spec = _suspend_spec(host, card)
    if (
        spec is None
        or spec.mana_cost is None
        or spec.counter_count is None
        or card.zone != "hand"
        or card.owner != seat
        or card.object_kind != "card"
        or card.annotations.get("copy_overrides") is not None
        or not _suspend_timing_is_legal(host, seat, card)
        or not host._cost_is_affordable(
            seat,
            spec.mana_cost,
            spend_context=None,
        )
    ):
        return None
    return ActionOffer(
        action_id=f"suspend:{card.ref}:{spec.ability_id}",
        action="suspend",
        seat=seat,
        label=(
            f"Suspend {card.printed_name} with {spec.counter_count} time "
            f"counter(s) — {spec.cost_text}"
        ),
        expiry_revision=host.state.revision,
        payload=freeze_json(
            {
                "kind": "suspend",
                "card": card.ref,
                "ability": spec.ability_id,
                "cost": spec.cost_text,
                "requirements": dict(spec.mana_cost),
                "counter_count": spec.counter_count,
                "auto_pay": True,
            }
        ),
    )


def _replacement_selections(
    response: Mapping[str, Any],
    *,
    card_ref: str,
    counter_event_id: str,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    raw = response.get("_mana_replacement_selections") or {}
    if not isinstance(raw, Mapping):
        raise GameRuleError("Suspend replacement journal is malformed")
    zone_rows = [
        value
        for event_id, value in raw.items()
        if type(event_id) is str
        and event_id.startswith("zone.change:")
        and event_id.endswith(f":{card_ref}")
    ]
    if len(zone_rows) > 1:
        raise GameRuleError("Suspend zone-replacement journal is ambiguous")
    counter_rows = raw.get(counter_event_id, ())
    for values in (*zone_rows, counter_rows):
        if not isinstance(values, (list, tuple)):
            raise GameRuleError("Suspend replacement selections are malformed")
    return (
        tuple(zone_rows[0]) if zone_rows else (),
        tuple(counter_rows),
    )


def commit_suspend(
    host: SuspendHost,
    *,
    seat: str,
    response: Mapping[str, Any],
) -> None:
    host._check_priority(seat)
    ref = response.get("card") or response.get("id")
    if type(ref) is not str or not ref:
        raise GameRuleError("Suspend actions require a card ref")
    card = host._resolve_object(
        seat,
        ref,
        zones={"hand"},
        owned_only=True,
    )
    offer = build_suspend_offer(host, seat, card)
    if offer is None:
        raise GameRuleError("This card cannot currently be suspended")
    supplied = response.get("proposal_fingerprint")
    if supplied is not None:
        expiry = response.get("expiry_revision", offer.expiry_revision)
        if (
            type(expiry) is not int
            or host.state.revision not in {expiry, expiry + 1}
            or str(supplied)
            != replace(offer, expiry_revision=expiry).fingerprint
        ):
            raise GameRuleError("The advertised Suspend action is stale")
    spec = _suspend_spec(host, card)
    if (
        spec is None
        or spec.mana_cost is None
        or spec.counter_count is None
        or response.get("ability") != spec.ability_id
    ):
        raise GameRuleError("The Suspend contract changed before payment")
    payment_id = str(response.get("_mana_payment_id") or offer.fingerprint)
    counter_event_id = f"counter.place:{payment_id}:{card.ref}:suspend"
    zone_selections, counter_selections = _replacement_selections(
        response,
        card_ref=card.ref,
        counter_event_id=counter_event_id,
    )
    clear_mana_undo_stack(host.state.players[seat].stats)
    spent, activations = host._pay_for_cost(
        seat,
        spec.mana_cost,
        response,
        spend_context=None,
    )
    host.move_card(
        card.object_id,
        "exile",
        reason="Suspend special action",
        semantic_events=True,
        replacement_selections=zone_selections,
    )
    placed = 0
    if card.zone == "exile":
        try:
            prepared = prepare_counter_placements(
                host,
                (
                    CounterPlacementRequest(
                        subject_kind="permanent",
                        subject_id=card.object_id,
                        counter_name="time",
                        amount=spec.counter_count,
                        placing_player=seat,
                        source_ref=card.ref,
                        effect_generated=True,
                    ),
                ),
                selections=counter_selections,
                event_ids=(counter_event_id,),
            )
            results = commit_prepared_counter_placements(
                host,
                prepared,
                reason="Suspend special action",
            )
        except CounterPlacementError as exc:
            raise GameRuleError(str(exc)) from exc
        if len(results) != 1:
            raise StateInvariantError(
                "Suspend time-counter placement produced the wrong result"
            )
        placed = results[0].placed
    host._log(
        seat,
        "card.suspend",
        f"{seat} suspended {card.ref}.",
        {
            "card": card.ref,
            "destination": card.zone,
            "time_counters": placed,
            "requirements": dict(spec.mana_cost),
            "payment": {key: value for key, value in spent.items() if value},
            "mana_sources": [
                activation.get("source_ref") or activation.get("source")
                for activation in activations
            ],
        },
        importance=2,
        changed_objects=[card.object_id],
        changed_players=[seat],
    )
    host.turn_priority.complete_special_action(seat)


def suspend_upkeep_trigger_items(
    host: SuspendHost,
    *,
    active_player: str,
) -> tuple[StackItem, ...]:
    result: list[StackItem] = []
    for object_id in tuple(host.state.players[active_player].zones["exile"]):
        card = host.state.cards[object_id]
        spec = _suspend_spec(host, card)
        if (
            spec is None
            or card.owner != active_player
            or card.counters.get("time", 0) <= 0
        ):
            continue
        ref = host._next_ref("S")
        result.append(
            StackItem(
                stack_id=host._stable_runtime_id("stack", ref),
                ref=ref,
                kind="triggered_ability",
                controller=card.owner,
                label=f"{card.printed_name} — remove a time counter",
                source_object_id=card.object_id,
                semantic_key=SUSPEND_UPKEEP_SEMANTIC_KEY,
                visibility=list(host.seats),
                context={
                    "event": "step.begin",
                    "source_logical_object_id": card.logical_object_id,
                    "suspend_spec": spec.to_dict(),
                },
            )
        )
    return tuple(result)


def _current_trigger_card(
    host: SuspendHost,
    item: StackItem,
) -> tuple[CardInstance | None, FixedCastLifecycleSpec | None]:
    card = host.state.cards.get(item.source_object_id or "")
    raw_spec = item.context.get("suspend_spec")
    try:
        spec = (
            FixedCastLifecycleSpec.from_dict(raw_spec)
            if isinstance(raw_spec, Mapping)
            else None
        )
    except (TypeError, ValueError):
        spec = None
    if (
        card is None
        or spec is None
        or spec.kind is not FixedCastLifecycleKind.SUSPEND
        or card.zone != "exile"
        or card.logical_object_id
        != item.context.get("source_logical_object_id")
        or card.owner != item.controller
    ):
        return None, None
    return card, spec


def _finish_intrinsic_trigger(
    host: SuspendHost,
    item: StackItem,
    *,
    outcome: str,
) -> None:
    complete_stack_resolution(
        host,
        item=item,
        destination=None,
        prepared_replacement=None,
    )
    host._log(
        item.controller,
        "suspend.trigger.resolve",
        f"Resolved {item.ref}: {item.label} ({outcome}).",
        {"stack": item.ref, "outcome": outcome},
        importance=2,
        changed_players=[item.controller],
    )
    if not host._stabilize():
        host._grant_priority(host.state.active_player)


def suspend_last_counter_trigger_items(
    host: SuspendHost,
    transitions: Sequence[Any],
) -> tuple[StackItem, ...]:
    result: list[StackItem] = []
    for transition in transitions:
        if (
            transition.subject_kind != "permanent"
            or transition.counter_name != "time"
            or transition.before <= 0
            or transition.after != 0
        ):
            continue
        card = host.state.cards.get(transition.subject_id)
        if card is None or card.zone != "exile":
            continue
        spec = _suspend_spec(host, card)
        if spec is None:
            continue
        ref = host._next_ref("S")
        result.append(
            StackItem(
                stack_id=host._stable_runtime_id("stack", ref),
                ref=ref,
                kind="triggered_ability",
                controller=card.owner,
                label=f"{card.printed_name} — cast from Suspend",
                source_object_id=card.object_id,
                semantic_key=SUSPEND_CAST_SEMANTIC_KEY,
                visibility=list(host.seats),
                context={
                    "event": "counter.removed",
                    "source_logical_object_id": card.logical_object_id,
                    "suspend_spec": spec.to_dict(),
                },
            )
        )
    return tuple(result)


def resolve_suspend_upkeep_trigger(
    host: SuspendHost,
    item: StackItem,
) -> None:
    card, _spec = _current_trigger_card(host, item)
    if card is None or card.counters.get("time", 0) <= 0:
        _finish_intrinsic_trigger(host, item, outcome="source_unavailable")
        return
    try:
        plan = plan_counter_removal_effect(
            host,
            CounterRemoval(
                object_id=card.object_id,
                counter_name="time",
                amount=1,
                expected_zone="exile",
                expected_logical_object_id=card.logical_object_id,
            ),
        )
        result = commit_counter_removal_effect(host, plan)
    except CounterRemovalError as exc:
        raise GameRuleError(str(exc)) from exc
    triggers = suspend_last_counter_trigger_items(host, plan.counter_plan.transitions)
    enqueue_trigger_batch(host, triggers)
    _finish_intrinsic_trigger(
        host,
        item,
        outcome=f"removed_{result.removed}",
    )


def resolve_suspend_cast_trigger(
    host: Any,
    item: StackItem,
) -> None:
    card, _spec = _current_trigger_card(host, item)
    if card is None:
        _finish_intrinsic_trigger(host, item, outcome="source_unavailable")
        return
    options = host._one_shot_exile_cast_options(
        actor=item.controller,
        card=card,
        maximum_mana_value=None,
    )
    if not options:
        host._finish_one_shot_exile_cast_resolution(
            item=item,
            producer=SUSPEND_EXILE_CAST_PRODUCER,
            cleanup_cards=(),
            outcome="cast_unavailable",
            candidate_ref=card.ref,
        )
        return
    host._begin_one_shot_exile_cast_choice(
        item=item,
        card=card,
        cleanup_cards=(),
        maximum_mana_value=None,
        producer=SUSPEND_EXILE_CAST_PRODUCER,
    )


__all__ = [
    "build_suspend_offer",
    "commit_suspend",
    "resolve_suspend_cast_trigger",
    "resolve_suspend_upkeep_trigger",
    "SUSPEND_CAST_SEMANTIC_KEY",
    "SUSPEND_EXILE_CAST_PRODUCER",
    "SUSPEND_HASTE_CONTEXT_FIELD",
    "SUSPEND_UPKEEP_SEMANTIC_KEY",
    "suspend_last_counter_trigger_items",
    "suspend_upkeep_trigger_items",
]
