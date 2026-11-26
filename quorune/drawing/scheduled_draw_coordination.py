"""Complete a scheduled draw result before returning to the original draw."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..replacement import FrozenMap, ReplacementEffectError, next_batch_replacement_choice
from ..replacement.immutable import thaw_value
from ..replacement.ordering import ReplacementChoiceRequired, replacement_choice_payload
from ..semantic_runtime.intents import LifeChangeIntent, MoveObjectsSimultaneouslyIntent
from ..semantic_runtime.token_creation_intent import CreateTokenIntent
from ..next_draw_replacement_model import PUBLIC_PERMANENT_RETURN_RESULT, ResolvedNextDrawReplacement
from ..rules.next_draw_replacement_shapes import require_scheduled_draw_result_ownership
from ..zone_trigger_events import ZoneTransitionKind
from .continuation import DrawResume
from .model import DrawError
from .scheduled_permanent_return import (
    RETURN_OFFERED,
    scheduled_permanent_return_candidates,
    scheduled_permanent_return_effect,
    selected_scheduled_permanent_returns,
)


SCHEDULED_DRAW_RESUME = "scheduled_draw_result"
SCHEDULED_DRAW_EVENT = "card.draw.replaced.scheduled_result"
_RESULT_EVENT_KINDS = {"gain_life": "life.change", "create_token": "token.create",
                       "discard_each_opponent": "zone.change", PUBLIC_PERMANENT_RETURN_RESULT: "zone.change"}


@dataclass(frozen=True, slots=True)
class ScheduledDrawContinuation:
    grant: ResolvedNextDrawReplacement
    after: DrawResume
    turn_sequence: int
    replaced_event: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.grant, ResolvedNextDrawReplacement)
            or not isinstance(self.after, DrawResume)
            or type(self.turn_sequence) is not int
            or self.turn_sequence < 0
            or type(self.replaced_event) is not int or self.replaced_event < 1
        ):
            raise DrawError("A scheduled draw result needs its consumed grant, turn, and original draw tail")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "grant": self.grant.to_dict(),
            "after": self.after.to_dict(),
            "turn_sequence": self.turn_sequence,
            "replaced_event": self.replaced_event,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ScheduledDrawContinuation":
        if (
            not isinstance(value, Mapping)
            or set(value) != {"schema_version", "grant", "after", "turn_sequence", "replaced_event"}
            or type(value["schema_version"]) is not int
            or value["schema_version"] != 1
        ):
            raise DrawError("Malformed scheduled draw result continuation")
        return cls(
            ResolvedNextDrawReplacement.from_dict(value["grant"]),
            DrawResume.from_dict(value["after"]),
            value["turn_sequence"],
            value["replaced_event"],
        )

    def validate(self, host) -> None:
        require_scheduled_draw_result_ownership(self.grant.result)
        event = next((row for row in reversed(host.state.events) if row.event_id == self.replaced_event), None)
        if (
            self.turn_sequence != host.state.turn_sequence
            or event is None
            or event.code != SCHEDULED_DRAW_EVENT
            or event.actor != self.grant.controller
            or event.details.get("grant_fingerprint") != self.grant.fingerprint
            or self.grant.controller not in host.active_seats
            or any(
                effect.effect_id == self.grant.effect_id
                for effect in host.state.continuous_effects or ()
            )
        ):
            raise DrawError("The scheduled draw completion no longer owns a consumed grant")


def _discard_effect(grant: ResolvedNextDrawReplacement) -> dict[str, Any]:
    return {
        "op": "choose_cards_apnap",
        "players": "opponents",
        "actor": grant.controller,
        "zone": "hand",
        "then": "discard",
        "count": grant.result.payload["count"],
        "hidden": True,
        "prompt": "Choose cards to discard.",
    }


def _eligible_hands(host, controller: str) -> dict[str, list[str]]:
    return {
        seat: [host.state.cards[object_id].ref for object_id in host.state.players[seat].zones["hand"]]
        for seat in host.apnap_order()
        if seat != controller
    }


def begin_scheduled_draw_result(host, grant: ResolvedNextDrawReplacement, after: DrawResume) -> bool:
    """Return true when an outcome pauses and takes ownership of the draw tail."""

    step = ScheduledDrawContinuation(grant, after, host.state.turn_sequence, host.state.event_sequence)
    step.validate(host)
    if grant.result.kind == PUBLIC_PERMANENT_RETURN_RESULT:
        effect = scheduled_permanent_return_effect(grant.controller)
        offered = scheduled_permanent_return_candidates(host, effect, host.apnap_order())
        if not any(offered.values()):
            return False
        host._issue_apnap_choice(effect=effect, continuation={SCHEDULED_DRAW_RESUME: step.to_dict(), RETURN_OFFERED: offered})
        return True
    if grant.result.kind == "discard_each_opponent":
        if not any(_eligible_hands(host, grant.controller).values()):
            return False
        host._issue_apnap_choice(
            effect=_discard_effect(grant),
            continuation={SCHEDULED_DRAW_RESUME: step.to_dict()},
        )
        return True
    return _attempt_outcome(host, step)


def _execute_result(host, step, *, refs, selections) -> None:
    grant = step.grant
    actor = grant.controller
    payload = grant.result.payload
    reason = "scheduled draw replacement"
    if grant.result.kind == "gain_life":
        host.apply_life_change_intent(LifeChangeIntent(
            actor, actor, payload["amount"], reason, grant.source_ref,
            replacement_selections=selections,
        ))
    elif grant.result.kind == "create_token":
        token = payload["token"]
        host.create_token_intent(CreateTokenIntent(
            actor, actor, token["name"], token["quantity"], reason,
            characteristics=FrozenMap(token["characteristics"]),
            replacement_selections=selections,
        ))
    elif grant.result.kind == PUBLIC_PERMANENT_RETURN_RESULT and refs:
        host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=refs, expected_zones=("battlefield",), destination="hand",
            reason=reason, replacement_selections=selections,
        ))
    elif grant.result.kind == "discard_each_opponent" and refs:
        host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=refs, expected_zones=("hand",), destination="graveyard",
            reason=reason, replacement_selections=selections,
            transition_kind=ZoneTransitionKind.DISCARD,
            discard_effect_origin=grant.discard_origin,
        ))



def _selected_scheduled_discards(host, state):
    step = ScheduledDrawContinuation.from_dict(state["resume"][SCHEDULED_DRAW_RESUME])
    step.validate(host)
    grant = step.grant
    collected = state["selected"]
    hands = _eligible_hands(host, grant.controller)
    if (
        grant.result.kind != "discard_each_opponent"
        or state["effect"] != _discard_effect(grant)
        or list(hands) != state["player_order"]
        or set(collected) != set(hands)
    ):
        raise DrawError("Scheduled discard choices changed their result or players")
    refs = []
    count = grant.result.payload["count"]
    for seat, hand in hands.items():
        selected = collected[seat]
        if (
            not isinstance(selected, (list, tuple))
            or len(selected) != min(count, len(hand))
            or any(type(ref) is not str or ref not in hand for ref in selected)
            or len(set(selected)) != len(selected)
        ):
            raise DrawError("Scheduled discard choices changed their eligible hand")
        refs.extend(selected)
    return step, tuple(refs)


def complete_scheduled_apnap_result(host, state) -> None:
    from .coordinator import resume_after_draw

    select = selected_scheduled_permanent_returns if RETURN_OFFERED in state["resume"] else _selected_scheduled_discards
    step, refs = select(host, state)
    if not _attempt_outcome(host, step, refs=refs):
        resume_after_draw(host, step.after)

def _attempt_outcome(host, step, *, refs=(), selections=()) -> bool:
    try:
        _execute_result(host, step, refs=refs, selections=selections)
    except ReplacementChoiceRequired as required:
        chooser = required.pending.choice.chooser
        host.permissions.issue(
            kind="replacement.order", role="pilot", actors=[chooser], allowed_actions=["choose"],
            payload_by_actor={chooser: replacement_choice_payload(required.pending, required.effects)},
            continuation={
                "replacement_resume_kind": SCHEDULED_DRAW_RESUME,
                "scheduled_draw": step.to_dict(),
                "object_refs": list(refs),
                "replacement_selections": list(selections),
                "replacement_batch": required.batch.to_dict(),
                "replacement_effects": [effect.to_dict() for effect in required.effects],
            },
        )
        return True
    return False


def decode_scheduled_draw_replacement(continuation_type, value, batch, effects):
    from ..replacement.replay import _decode_combat_selections

    try:
        step = ScheduledDrawContinuation.from_dict(value["scheduled_draw"])
        refs = value["object_refs"]
        if (
            not isinstance(refs, (list, tuple))
            or any(type(ref) is not str or not ref for ref in refs)
            or len(set(refs)) != len(refs)
            or bool(refs) != (step.grant.result.kind in {"discard_each_opponent", PUBLIC_PERMANENT_RETURN_RESULT})
            or any(event.kind != _RESULT_EVENT_KINDS[step.grant.result.kind] for event in batch.events)
        ):
            raise DrawError("The replacement is outside its exact scheduled draw result")
    except (KeyError, TypeError, ValueError) as exc:
        raise ReplacementEffectError(str(exc)) from exc
    return continuation_type(
        batch=batch, effects=effects, resume_kind=SCHEDULED_DRAW_RESUME,
        effect=FrozenMap({"scheduled_draw": step.to_dict(), "object_refs": refs}),
        replacement_selections=_decode_combat_selections(value),
    )


def _replacement_batch_shape(batch):
    """Compare typed event trees across the revision added by a choice command."""

    value = batch.to_dict()
    event_ids = {}

    def collect(events):
        for event in events:
            event_ids[event["event_id"]] = len(event_ids)
            collect(event.get("children", ()))

    def pin(item):
        if isinstance(item, Mapping):
            return {key: pin(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [pin(child) for child in item]
        if type(item) is str and item in event_ids:
            return ("event", event_ids[item])
        return item

    collect(value["events"])
    value.pop("batch_id")
    return pin(value)


def resume_scheduled_draw_replacement(host, restored, selection, *, error_type):
    from .coordinator import resume_after_draw

    value = restored.thaw_effect()
    step = ScheduledDrawContinuation.from_dict(value["scheduled_draw"])
    refs = tuple(value["object_refs"])
    selections = tuple(thaw_value(item) for item in restored.replacement_selections)
    try:
        step.validate(host)
        try:
            _execute_result(host, step, refs=refs, selections=selections)
        except ReplacementChoiceRequired as required:
            previous = next_batch_replacement_choice(restored.batch, restored.effects)
            if (
                previous is None
                or required.effects != restored.effects
                or _replacement_batch_shape(required.batch) != _replacement_batch_shape(restored.batch)
            ):
                raise DrawError("The scheduled draw outcome's replacement inputs changed") from required
        else:
            raise DrawError("The scheduled draw outcome no longer has this replacement choice")
        if not _attempt_outcome(host, step, refs=refs, selections=(*selections, selection)):
            resume_after_draw(host, step.after)
    except (DrawError, ReplacementEffectError) as exc:
        raise error_type(str(exc)) from exc
