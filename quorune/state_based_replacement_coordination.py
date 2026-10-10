from __future__ import annotations

"""Authenticated suspension of state-based destruction replacement choices."""

from typing import Any, Mapping, Sequence
from contextvars import ContextVar

from .replacement.immutable import thaw_value
from .replacement.model import ReplacementEffectError
from .replacement.ordering import replacement_choice_payload
from .replacement.ordering import ReplacementChoiceRequired
_CURRENT_SELECTIONS: ContextVar[tuple[Any, ...]] = ContextVar("state_based_replacement_selections", default=())


def log_state_based_counter_annihilation(host: Any, batch: Any, changes: Sequence[Any]) -> None:
    host._log(None, "state.counters_annihilated",
              "State-based actions removed opposing +1/+1 and -1/-1 counters.",
              {"changes": changes}, importance=2,
              changed_objects=[object_id for object_id, _ in batch.counter_pairs_to_remove
                               if host.state.cards[object_id].zone == "battlefield"])


def prepare_state_based_execution_or_choice(host: Any, batch: Any):
    from .state_based_execution import prepare_state_based_execution
    selected = _CURRENT_SELECTIONS.get()
    _CURRENT_SELECTIONS.set(())
    try:
        return prepare_state_based_execution(host, batch, replacement_selections=selected)
    except ReplacementChoiceRequired as required:
        issue_state_based_replacement_choice(host, required, selected)
        return None


def state_based_replacement_frame(host: Any) -> dict[str, Any]:
    battlefield = []
    for card in sorted(host.state.cards.values(), key=lambda value: value.object_id):
        if card.zone != "battlefield":
            continue
        data = host._effective_card_data(card)
        from .ability_fragments import ability_fragment_to_dict, canonical_ability_fragments
        battlefield.append({
            "object_id": card.object_id, "logical_object_id": card.logical_object_id,
            "controller": card.controller, "phased_out": card.phased_out,
            "attached_to": card.attached_to, "marked_damage": card.marked_damage,
            "counters": dict(card.counters), "regeneration_shields": card.regeneration_shields,
            "type_line": data.get("type_line"), "keywords": list(data.get("keywords", ())),
            "power": data.get("power"), "toughness": data.get("toughness"),
            "ability_fragments": [ability_fragment_to_dict(fragment) for fragment in canonical_ability_fragments(data.get("ability_fragments", ()))],
        })
    return {"active_player": host.state.active_player, "phase": host.state.phase, "step": host.state.step,
            "turn_sequence": host.state.turn_sequence, "stack_refs": [item.ref for item in host.state.stack],
            "battlefield": battlefield}


def issue_state_based_replacement_choice(host: Any, required: Any, selections: Sequence[Any]) -> None:
    chooser = required.pending.choice.chooser
    host.permissions.issue(
        kind="replacement.order", role="pilot", actors=[chooser], allowed_actions=["choose"],
        payload_by_actor={chooser: replacement_choice_payload(required.pending, required.effects)},
        continuation={"replacement_resume_kind": "state_based_destruction",
                      "state_based_frame": state_based_replacement_frame(host),
                      "replacement_selections": [thaw_value(value) for value in selections],
                      "replacement_batch": required.batch.to_dict(),
                      "replacement_effects": [effect.to_dict() for effect in required.effects]},
    )


def resume_state_based_replacement(host: Any, restored: Any, selection: Any, *, error_type: type[Exception]) -> None:
    if thaw_value(restored.state_based_frame) != state_based_replacement_frame(host):
        raise error_type("State-based replacement inputs changed before resume")
    try:
        token = _CURRENT_SELECTIONS.set((*restored.replacement_selections, selection))
        try:
            waiting = host._stabilize()
        finally:
            _CURRENT_SELECTIONS.reset(token)
    except ReplacementEffectError as exc:
        raise error_type(str(exc)) from exc
    if not waiting:
        host._grant_priority(host.state.active_player)


def decode_state_based_replacement(continuation_type, value: Mapping[str, Any], batch: Any, effects: Any):
    frame = value["state_based_frame"]
    expected = {"active_player", "phase", "step", "turn_sequence", "stack_refs", "battlefield"}
    if not isinstance(frame, Mapping) or set(frame) != expected:
        raise ReplacementEffectError("State-based replacement frame fields are malformed")
    if (frame["active_player"] not in batch.apnap_order or type(frame["turn_sequence"]) is not int
            or frame["turn_sequence"] < 0 or not isinstance(frame["battlefield"], (list, tuple))):
        raise ReplacementEffectError("State-based replacement frame identity is malformed")
    if any(event.kind != "permanent.destroy" for event in batch.events):
        raise ReplacementEffectError("State-based destruction continuation has wrong event kind")
    from .replacement.replay import _decode_combat_selections
    from .replacement.immutable import FrozenMap
    return continuation_type(batch=batch, effects=effects, resume_kind="state_based_destruction",
                             state_based_frame=FrozenMap(frame), replacement_selections=_decode_combat_selections(value))
