from __future__ import annotations

"""Authenticated suspension of state-based destruction and zone choices."""

from typing import Any, Mapping, Sequence
from contextvars import ContextVar

from .replacement.immutable import thaw_value
from .replacement.model import ReplacementEffectError
from .replacement.ordering import replacement_choice_payload
from .replacement.ordering import ReplacementChoiceRequired
_CURRENT_SELECTIONS: ContextVar[tuple[Any, ...]] = ContextVar("state_based_replacement_selections", default=())
_CURRENT_ZONE_SELECTIONS: ContextVar[tuple[Any, ...]] = ContextVar("state_based_zone_replacement_selections", default=())


def log_state_based_counter_annihilation(host: Any, batch: Any, changes: Sequence[Any]) -> None:
    host._log(None, "state.counters_annihilated",
              "State-based actions removed opposing +1/+1 and -1/-1 counters.",
              {"changes": changes}, importance=2,
              changed_objects=[object_id for object_id, _ in batch.counter_pairs_to_remove
                               if host.state.cards[object_id].zone == "battlefield"])


def prepare_state_based_execution_or_choice(host: Any, batch: Any):
    from .state_based_execution import prepare_state_based_execution
    selected = _CURRENT_SELECTIONS.get()
    zone_selected = _CURRENT_ZONE_SELECTIONS.get()
    _CURRENT_SELECTIONS.set(())
    _CURRENT_ZONE_SELECTIONS.set(())
    try:
        return prepare_state_based_execution(host, batch, replacement_selections=selected,
                                             zone_replacement_selections=zone_selected)
    except ReplacementChoiceRequired as required:
        issue_state_based_replacement_choice(host, required, selected, zone_selected)
        return None


def prepare_state_based_zone_replacements(host, destruction, companions, saga_ids, selections):
    from .destruction import _canonical_selections
    from .semantic_runtime.zone_replacements import prepare_zone_change_replacement_batch
    from .zone_trigger_events import ZoneTransitionKind
    selected = _canonical_selections(selections)
    order = destruction.destruction_event_order or destruction.destroyed_object_ids
    changes = tuple((object_id, 'graveyard') for object_id in order) + tuple(companions)
    if not changes:return ()
    prepared = prepare_zone_change_replacement_batch(host, changes,
            transition_kinds={object_id: ZoneTransitionKind.SACRIFICE for object_id in saga_ids},
            selections=tuple(thaw_value(value) for value in selected))
    return tuple(prepared.items())


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


def issue_state_based_replacement_choice(host: Any, required: Any, selections: Sequence[Any], zone_selections: Sequence[Any] = ()) -> None:
    chooser = required.pending.choice.chooser
    event_kinds={event.kind for event in required.batch.events}
    if event_kinds not in ({'zone.change'},{'permanent.destroy'}):
        raise ReplacementEffectError('State-based replacement event domain is unsupported')
    zone_choice = event_kinds == {'zone.change'}
    continuation={"replacement_resume_kind": "state_based_zone_change" if zone_choice else "state_based_destruction",
                  "state_based_frame": state_based_replacement_frame(host),
                  "replacement_selections": [thaw_value(value) for value in (zone_selections if zone_choice else selections)],
                  "replacement_batch": required.batch.to_dict(),
                  "replacement_effects": [effect.to_dict() for effect in required.effects]}
    if zone_choice:continuation['state_based_destruction_selections']=[thaw_value(value) for value in selections]
    host.permissions.issue(
        kind="replacement.order", role="pilot", actors=[chooser], allowed_actions=["choose"],
        payload_by_actor={chooser: replacement_choice_payload(required.pending, required.effects)},
        continuation=continuation,
    )


def resume_state_based_replacement(host: Any, restored: Any, selection: Any, *, error_type: type[Exception]) -> None:
    if thaw_value(restored.state_based_frame) != state_based_replacement_frame(host):
        raise error_type("State-based replacement inputs changed before resume")
    try:
        zone_choice = restored.resume_kind == 'state_based_zone_change'
        token = _CURRENT_SELECTIONS.set(restored.state_based_destruction_selections if zone_choice else (*restored.replacement_selections, selection))
        zone_token = _CURRENT_ZONE_SELECTIONS.set((*restored.replacement_selections, selection) if zone_choice else ())
        try:
            waiting = host._stabilize()
        finally:
            _CURRENT_SELECTIONS.reset(token)
            _CURRENT_ZONE_SELECTIONS.reset(zone_token)
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
    zone_choice = value['replacement_resume_kind'] == 'state_based_zone_change'
    if any(event.kind != ('zone.change' if zone_choice else 'permanent.destroy') for event in batch.events):
        raise ReplacementEffectError("State-based destruction continuation has wrong event kind")
    from .replacement.replay import _decode_combat_selections
    from .replacement.immutable import FrozenMap
    prior = _decode_combat_selections({'replacement_selections':value['state_based_destruction_selections']}) if zone_choice else ()
    return continuation_type(batch=batch, effects=effects, resume_kind=value['replacement_resume_kind'],
                             state_based_frame=FrozenMap(frame), replacement_selections=_decode_combat_selections(value),
                             state_based_destruction_selections=prior)
