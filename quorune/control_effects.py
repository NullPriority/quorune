from __future__ import annotations

"""Resolution and attached-source control through the canonical layer-two owner."""

from dataclasses import replace
from typing import Any, Mapping, Protocol, Sequence

from .continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin,
    ContinuousObjectIdentity, ContinuousOperation, Layer,
)
from .continuous_effect_state import (
    ResolutionEffectSource, active_resolution_effects, commit_continuous_effect,
)
from .continuous_effects import order_continuous_effects
from .object_predicate import ObjectQuerySpec
from .source_continuity import SourceContinuityHistory, SourceContinuitySnapshot
from .model import CONTROL_HISTORY_VERSION
from . import control_history
from .errors import GameRuleError
from .attached_control import static_attachment_control_effects, layer_two_controller_map


class ControlEffectError(ValueError):
    pass


class ControlEffectHost(Protocol):
    state: Any

    @property
    def active_seats(self) -> Sequence[str]: ...
    def _next_ref(self, prefix: str) -> str: ...
    def _next_zone_timestamp(self) -> int: ...
    def _resolve_object(self, actor: str, ref: str, *, zones: set[str]) -> Any: ...
    def _require_seat(self, seat: str, *, in_game: bool) -> None: ...
    def _remove_object_from_combat(self, card: Any, *, reason: str) -> None: ...
    def _log(self, seat: str | None, kind: str, message: str, data: Mapping[str, Any], **kwargs: Any) -> None: ...
    def move_card(self, object_id: str, zone: str, *, reason: str, log: bool) -> Any: ...


_ORIGIN_PREFIX = "control-origin:"
_EFFECT_PREFIX = "control-effect:"
_DURATIONS = {
    ContinuousEffectDuration.UNTIL_END_OF_TURN,
    ContinuousEffectDuration.ZONE_OBJECT,
    *(duration for duration in ContinuousEffectDuration if duration.source_bound),
}


def change_control(host: ControlEffectHost, object_id: str, controller: str, *, reason: str = "") -> None:
    """Route current custody through layer two; preserve historical execution."""
    if host.state.control_history_version == CONTROL_HISTORY_VERSION:
        card = host.state.cards[object_id]
        gain_control_of_refs(
            host, actor=controller, object_refs=(card.ref,), controller=controller,
            duration=ContinuousEffectDuration.ZONE_OBJECT,
            source=ResolutionEffectSource(stack_ref=f"direct:{card.ref}"),
            reason=reason or "direct control change",
        )
    else:
        _commit_control_change(host, object_id, controller, reason=reason)


def _commit_control_change(host: ControlEffectHost, object_id: str, controller: str, *, reason: str) -> None:
    host._require_seat(controller, in_game=True)
    card = host.state.cards[object_id]
    if card.zone != "battlefield":
        raise GameRuleError("Only battlefield permanents have controllers")
    previous = card.controller
    if previous != controller:
        host._remove_object_from_combat(card, reason="control changed")
        host.state.players[previous].zones["battlefield"].remove(object_id)
        host.state.players[controller].zones["battlefield"].append(object_id)
        card.controller = controller
    control_history.record_control_change(
        host.state, card, host._next_zone_timestamp, previous_controller=previous,
    )
    record_source_transition(host, card, control_changed=previous != controller)
    host._log(
        None, "control.change", f"Control of {card.ref} changed {previous} → {controller}.",
        {"object": card.ref, "from": previous, "to": controller, "reason": reason},
        importance=2, changed_objects=[object_id], changed_players=[previous, controller],
    )


def source_continuity_snapshot(card: Any) -> SourceContinuitySnapshot:
    if card.source_continuity is None:
        card.source_continuity = SourceContinuityHistory()
    return card.source_continuity.snapshot()


def _contains_source_duration(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("op") == "gain_control":
            try:
                if ContinuousEffectDuration(value.get("duration")).source_bound:
                    return True
            except (TypeError, ValueError):
                pass
        return any(_contains_source_duration(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_source_duration(child) for child in value)
    return False


def capture_control_duration(host: Any, program: Any, source: Any, logical_id: str | None):
    if program is None or not (
        _contains_source_duration(program.effects) or _contains_source_duration(program.target_schema)
    ):
        return False, None
    current = source is not None and source.logical_object_id == logical_id
    return True, source_continuity_snapshot(source) if current else None


def pin_pending_control_duration(host: Any, item: Any, *, reset: bool = False) -> None:
    """Capture continuity when a represented ability first exists, before priority."""
    if "control_duration_snapshot" in item.context and not reset:
        return
    program = host.semantics.get(item.semantic_key)
    source = host.state.cards.get(item.source_object_id)
    logical_id = item.context.get("source_logical_object_id")
    tracked, snapshot = capture_control_duration(host, program, source, logical_id)
    if not tracked:
        return
    item.context["control_duration_snapshot"] = (
        snapshot.to_dict() if snapshot is not None else None
    )
    item.context.pop("control_duration_resolution_timestamp", None)


def begin_control_duration_resolution(host: Any, item: Any) -> None:
    if "control_duration_snapshot" in item.context:
        if "control_duration_resolution_timestamp" not in item.context:
            item.context["control_duration_resolution_timestamp"] = host._next_zone_timestamp()


def record_source_transition(
    host: ControlEffectHost, card: Any, *, control_changed: bool = False,
    previous_tapped: bool | None = None, previous_phased_out: bool | None = None,
) -> None:
    """Observe real transitions only for sources whose new abilities track them."""
    history = card.source_continuity
    if history is None:
        return
    updates = {}
    if control_changed:
        updates.update(control_epoch=history.control_epoch + 1,
                       last_control_timestamp=host._next_zone_timestamp())
    if previous_tapped is not None and previous_tapped != card.tapped:
        if previous_tapped:
            updates["untap_epoch"] = history.untap_epoch + 1
        else:
            updates["last_tap_timestamp"] = host._next_zone_timestamp()
    if previous_phased_out is not None and previous_phased_out != card.phased_out:
        if card.phased_out:
            updates["phase_epoch"] = history.phase_epoch + 1
        else:
            updates["last_phase_in_timestamp"] = host._next_zone_timestamp()
    if updates:
        card.source_continuity = replace(history, **updates)


def _source_is_current(host: ControlEffectHost, source: ResolutionEffectSource) -> Any | None:
    card = host.state.cards.get(source.object_id)
    if (card is None or card.logical_object_id != source.logical_object_id
            or card.zone != "battlefield" or card.phased_out):
        return None
    return card


def _duration_condition(
    card: Any, duration: ContinuousEffectDuration, controller: str,
    snapshot: SourceContinuitySnapshot, *, resolution_timestamp: int | None = None,
    projected_controller: str | None = None,
) -> bool:
    if duration.requires_source_controller and (projected_controller or card.controller) != controller:
        return False
    if duration.requires_tapped_source and not card.tapped:
        return False
    history = card.source_continuity
    if history is None:
        raise ControlEffectError("Duration source continuity is unavailable")
    continuous = (history.phase_epoch == snapshot.phase_epoch
                  and (not duration.requires_source_controller or history.control_epoch == snapshot.control_epoch)
                  and (not duration.requires_tapped_source or history.untap_epoch == snapshot.untap_epoch))
    if continuous:
        return True
    # CR 611.2b permits a duration to begin again during this resolution,
    # before the control instruction first applies. Already committed effects
    # pass no resolution timestamp and therefore never restart.
    beginnings = [history.last_phase_in_timestamp]
    if duration.requires_source_controller:
        beginnings.append(history.last_control_timestamp)
    if duration.requires_tapped_source:
        beginnings.append(history.last_tap_timestamp)
    return resolution_timestamp is not None and max(beginnings) > resolution_timestamp


def has_control_origin(state: Any, card: Any) -> bool:
    """Whether this incarnation has a journaled initial controller."""
    key = _ORIGIN_PREFIX + card.logical_object_id
    return any(effect.effect_id == key for effect in state.continuous_effects or ())


def _control_effect(
    *, effect_id: str, source_id: str, timestamp: int,
    identities: tuple[ContinuousObjectIdentity, ...], controller: str,
    duration: ContinuousEffectDuration,
    duration_source: ContinuousObjectIdentity | None = None,
    duration_controller: str | None = None,
    duration_history: SourceContinuitySnapshot | None = None,
) -> ContinuousEffect:
    return ContinuousEffect(
        effect_id=effect_id, source_id=source_id, layer=Layer.CONTROL,
        sublayer="2", timestamp=timestamp,
        operations=(ContinuousOperation("set_controller", controller),),
        origin=ContinuousEffectOrigin.RESOLUTION, duration=duration,
        applies=ObjectQuerySpec(zones=("battlefield",)), locked_objects=identities,
        duration_source=duration_source, duration_controller=duration_controller,
        duration_history=duration_history,
    )


def gain_control_of_refs(
    host: ControlEffectHost, *, actor: str, object_refs: Sequence[str],
    controller: str, duration: ContinuousEffectDuration,
    source: ResolutionEffectSource, reason: str,
    history_snapshot: SourceContinuitySnapshot | None = None,
    resolution_timestamp: int | None = None,
) -> tuple[str, ...]:
    """Lock the whole original set before any control acquisition commits."""
    if duration not in _DURATIONS or not isinstance(duration, ContinuousEffectDuration):
        raise ControlEffectError("Resolution control duration is unsupported")
    if actor not in host.active_seats or controller not in host.active_seats:
        raise ControlEffectError("Resolution control requires active principals")
    if not isinstance(source, ResolutionEffectSource):
        raise ControlEffectError("Resolution control requires typed source identity")
    if type(reason) is not str or not reason:
        raise ControlEffectError("Resolution control requires a reason")
    if resolution_timestamp is not None and (type(resolution_timestamp) is not int or resolution_timestamp < 0):
        raise ControlEffectError("Resolution control timestamp is malformed")
    journal = host.state.continuous_effects
    if journal is None:
        raise ControlEffectError("Resolution control journal is unavailable")
    if host.state.control_history_version != CONTROL_HISTORY_VERSION:
        raise ControlEffectError("Resolution control requires current retained custody history")
    refs = tuple(object_refs)
    if any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise ControlEffectError("Resolution control subjects must be unique references")
    cards = tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)
    if any(card.phased_out for card in cards):
        raise ControlEffectError("Resolution control cannot select phased-out objects")
    identities = tuple(ContinuousObjectIdentity(card.object_id, card.logical_object_id)
                       for card in cards)
    if len(set(identities)) != len(identities):
        raise ControlEffectError("Resolution control subjects must be unique incarnations")
    if not identities:
        return ()
    source_card = None
    if duration.source_bound:
        source_card = _source_is_current(host, source)
        if source_card is None:
            return ()
        if not isinstance(history_snapshot, SourceContinuitySnapshot):
            raise ControlEffectError("Source-bound control requires its retained original continuity")
        if not _duration_condition(source_card, duration, actor, history_snapshot,
                                   resolution_timestamp=resolution_timestamp):
            return ()

    # Initial custody is a zero-timestamp layer-two input, independent of a
    # card's owner. It survives effect expiration and never gives a departing
    # player control as an effect (CR 800.4a).
    origins = tuple(
        _control_effect(
            effect_id=_ORIGIN_PREFIX + card.logical_object_id,
            source_id=card.object_id, timestamp=0, identities=(identity,),
            controller=card.controller,
            duration=ContinuousEffectDuration.ZONE_OBJECT,
        )
        for card, identity in zip(cards, identities, strict=True)
        if not has_control_origin(host.state, card)
    )
    prototype = _control_effect(
        effect_id="control-prototype", source_id=source.object_id or source.stack_ref,
        timestamp=0, identities=identities, controller=controller, duration=duration,
        duration_source=(ContinuousObjectIdentity(source_card.object_id, source_card.logical_object_id)
                         if source_card is not None else None),
        duration_controller=actor if duration.requires_source_controller else None,
        duration_history=source_card.source_continuity.snapshot() if source_card is not None else None,
    )
    effect = replace(prototype, effect_id=_EFFECT_PREFIX + host._next_ref("CE"),
                     timestamp=host._next_zone_timestamp())
    additions = (*origins, effect)
    ids = tuple(value.effect_id for value in additions)
    if len(set(ids)) != len(ids) or set(ids).intersection(value.effect_id for value in journal):
        raise ControlEffectError("Resolution control journal identity is already committed")
    for value in additions:
        commit_continuous_effect(host.state, value)
    for card in cards:
        temporary = card.annotations.get("until_end_of_turn")
        if isinstance(temporary, dict):
            temporary.pop("control_previous", None)
    synchronize_control_effects(host, reason=reason)
    return tuple(card.ref for card in cards)


def _controller_map(host: ControlEffectHost, journal: Sequence[ContinuousEffect], static: Sequence[ContinuousEffect]) -> dict[str, str]:
    controllers=layer_two_controller_map(host,journal,static)
    if any(type(controller) is not str or controller not in host.state.players for controller in controllers.values()):
        raise ControlEffectError("Layer-two control has an unavailable principal")
    return controllers


def _establish_static_control_origins(host: ControlEffectHost, static: Sequence[ContinuousEffect]) -> None:
    """Retain initial custody before a live static attachment changes it."""
    for effect in static:
        identity=effect.related_object
        if identity is None:continue
        card=host.state.cards.get(identity.object_id)
        if card is None or card.logical_object_id!=identity.logical_object_id or has_control_origin(host.state,card):continue
        commit_continuous_effect(host.state,_control_effect(effect_id=_ORIGIN_PREFIX+identity.logical_object_id,
            source_id=card.object_id,timestamp=0,identities=(identity,),controller=card.controller,
            duration=ContinuousEffectDuration.ZONE_OBJECT))


def _expired_source_duration(
    host: ControlEffectHost, effect: ContinuousEffect, controllers: dict[str, str],
) -> bool:
    source = host.state.cards.get(effect.duration_source.object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != effect.duration_source.logical_object_id):
        return True
    projected = controllers.get(source.object_id, source.controller)
    if projected not in host.active_seats:
        return True
    return not _duration_condition(
        source, effect.duration, effect.duration_controller or projected,
        effect.duration_history, projected_controller=projected,
    )


def _next_source_expiration(host: ControlEffectHost, ending: Sequence[ContinuousEffect]) -> ContinuousEffect:
    """Settle a changing source before a duration depending on that source."""
    candidates = []
    for effect in ending:
        source = host.state.cards.get(effect.duration_source.object_id)
        source_is_current = (source is not None and source.zone == "battlefield"
                             and not source.phased_out
                             and source.logical_object_id == effect.duration_source.logical_object_id)
        prerequisites = tuple(
            other.effect_id for other in ending
            if source_is_current and other.effect_id != effect.effect_id
            and effect.duration_source in other.locked_objects
        )
        candidates.append(replace(effect, depends_on=prerequisites))
    ordered, _cycles = order_continuous_effects(candidates)
    return next(effect for effect in ending if effect.effect_id == ordered[0].effect_id)


def synchronize_control_effects(host: ControlEffectHost, *, reason: str) -> bool:
    """Compute monotonic source expiration before committing resulting custody."""
    if host.state.control_history_version != CONTROL_HISTORY_VERSION:
        return False
    static=static_attachment_control_effects(host)
    _establish_static_control_origins(host,static)
    journal = host.state.continuous_effects
    if not journal:
        return False
    retained = list(journal)
    expired = []
    # Each changing pass removes at least one source-bound effect. Expired
    # durations never reappear, so source cycles terminate without guessing.
    for _ in range(len(journal) + 1):
        controllers = _controller_map(host, retained, static)
        ending = [effect for effect in retained if isinstance(effect, ContinuousEffect)
                  and effect.duration.source_bound
                  and _expired_source_duration(host, effect, controllers)]
        if not ending:
            break
        # Removing every currently false duration can expose a transient
        # controller that never commits. Reevaluate after the prerequisite,
        # using the existing dependency/timestamp owner (CR 613.8b-c).
        next_ending = _next_source_expiration(host, ending)
        expired.append(next_ending)
        retained = [effect for effect in retained if effect.effect_id != next_ending.effect_id]
    else:
        raise ControlEffectError("Source-bound control did not converge")
    changes = {object_id: (controller if controller in host.active_seats else None)
               for object_id, controller in controllers.items()
               if controller != host.state.cards[object_id].controller or controller not in host.active_seats}
    dependencies = {object_id: set() for object_id in changes}
    for effect in expired:
        source_id = effect.duration_source.object_id
        if source_id in changes:
            for target in effect.locked_objects:
                if target.object_id in changes and target.object_id != source_id:
                    dependencies[target.object_id].add(source_id)
    order = []
    pending = set(changes)
    while pending:
        ready = [object_id for object_id in pending if not dependencies[object_id].intersection(pending)]
        if not ready:
            ready = list(pending)
        ready.sort(key=lambda object_id: (host.state.cards[object_id].ref, object_id))
        order.extend(ready)
        pending.difference_update(ready)
    journal[:] = retained
    for object_id in order:
        card = host.state.cards[object_id]
        if card.zone != "battlefield":
            continue
        controller = changes[object_id]
        if controller is None:
            host.move_card(object_id, "exile", reason="control returned to a player who left", log=True)
        else:
            _commit_control_change(host, object_id, controller, reason=reason)
    return bool(changes or expired)


def end_player_control_effects(state: Any, seat: str) -> int:
    """End control granted to a departing player, preserving initial custody."""
    journal = state.continuous_effects
    if journal is None or state.control_history_version != CONTROL_HISTORY_VERSION:
        return 0
    retained = [
        effect for effect in journal
        if not (isinstance(effect, ContinuousEffect) and effect.layer is Layer.CONTROL
                and effect.effect_id.startswith(_EFFECT_PREFIX)
                and any(operation.op == "set_controller" and operation.value == seat
                        for operation in effect.operations))
    ]
    ended = len(journal) - len(retained)
    state.continuous_effects = retained
    return ended


def control_set_query_is_closed(query: ObjectQuerySpec, *, actor: str) -> bool:
    from .rules.resolution_characteristic_shapes import fixed_resolution_characteristic_set_is_closed
    if query.owner is not None:
        return query == ObjectQuerySpec(zones=("battlefield",), owner=actor)
    if query.controller not in {None, actor} or query.excluded_controllers not in {(), (actor,)}:
        return False
    return fixed_resolution_characteristic_set_is_closed(query)


def execute_control_set(host: Any, intent: Any) -> tuple[str, ...]:
    """Retain this instruction's complete set across its printed substeps."""
    from .continuous_effect_state import (
        matching_battlefield_objects, ResolutionContinuousComponent,
        create_resolution_continuous_effect_components,
    )
    from .tap_state import untap_permanent, dispatch_tap_state_group
    from .semantic_runtime.control_intents import GainControlSetIntent
    if not isinstance(intent, GainControlSetIntent):
        raise ControlEffectError("Control-set execution requires a typed retained intent")
    if not control_set_query_is_closed(intent.predicate, actor=intent.actor):
        raise ControlEffectError("Control-set query is outside the public closed family")
    cards = matching_battlefield_objects(host, intent.predicate)
    identities = tuple(ContinuousObjectIdentity(card.object_id, card.logical_object_id) for card in cards)
    for step in intent.steps:
        current = tuple(
            host.state.cards[identity.object_id] for identity in identities
            if identity.object_id in host.state.cards
            and host.state.cards[identity.object_id].logical_object_id == identity.logical_object_id
            and host.state.cards[identity.object_id].zone == "battlefield"
            and not host.state.cards[identity.object_id].phased_out
        )
        if step == "gain_control":
            gain_control_of_refs(
                host, actor=intent.actor, object_refs=tuple(card.ref for card in current),
                controller=intent.controller, duration=intent.duration,
                source=intent.source, reason=intent.reason,
            )
        elif step == "untap":
            changed = tuple(card for card in current if untap_permanent(
                host, card, actor=intent.actor, reason=intent.reason,
            ))
            dispatch_tap_state_group(host, changed, tapped=False, reason=intent.reason)
        elif step == "haste":
            create_resolution_continuous_effect_components(
                host, source=intent.source, targets=current,
                components=(ResolutionContinuousComponent(
                    Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Haste"),),
                ),),
            )
        else:
            raise ControlEffectError("Control-set instruction step is unsupported")
    return tuple(card.ref for card in cards)
