from __future__ import annotations

"""Plan the CR 502.1 snapshot, then commit one simultaneous status change."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .attachments import attach_objects, attach_to_player, attached_player_seat, detach_object
from .continuous_effect_model import ContinuousObjectIdentity
from .control_effects import (
    controlled_source_duration_is_current, prepare_controlled_source_untap_duration,
    record_source_transition, synchronize_control_effects,
)
from .phasing_model import PhasingRecord
from .semantic_runtime.untap_steps import current_printed_phasing_object_ids
from .source_control_phase_in_model import SOURCE_CONTROL_PHASE_OUT_SUBJECT, SourceControlPhaseInRestriction
from .source_continuity import SourceContinuitySnapshot
from .source_tap_effect import RUNTIME_SOURCE_CONTINUITY_FIELD

if TYPE_CHECKING:
    from .model import CardInstance, GameState


_ATTACHMENT_TYPES = frozenset({"aura", "equipment", "fortification"})


@dataclass(frozen=True, slots=True)
class PhaseTransition:
    object: ContinuousObjectIdentity
    record: PhasingRecord
    out: bool


@dataclass(frozen=True, slots=True)
class PhasingPlan:
    active: str
    transitions: tuple[PhaseTransition, ...] = ()
    unsupported: str | None = None


def _identity(card: CardInstance) -> ContinuousObjectIdentity:
    return ContinuousObjectIdentity(card.object_id, card.logical_object_id)


def _host_incarnation(cards, attachment: ContinuousObjectIdentity | None) -> CardInstance | None:
    if attachment is None:
        return None
    target = cards.get(attachment.object_id)
    if target is None or target.logical_object_id != attachment.logical_object_id:
        return None
    return target


def _record_outgoing(card, battlefield, players, outgoing, children):
    target = battlefield.get(card.attached_to)
    if target is not None:
        indirect = target.object_id in outgoing and card.object_id in children.get(target.object_id, ())
        return PhasingRecord(card.controller, attached_object=_identity(target), indirect=indirect)
    player = attached_player_seat(card)
    return PhasingRecord(card.controller, attached_player=player if player in players else None)


def _attachment_children(host, battlefield):
    children = {}
    for card in battlefield.values():
        if card.phased_out or card.attached_to not in battlefield:
            continue
        _, subtypes, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
        if _ATTACHMENT_TYPES.intersection(subtypes):
            children.setdefault(card.attached_to, []).append(card.object_id)
    return children


def _expand_groups(roots, children):
    included = set(roots)
    waiting = list(roots)
    while waiting:
        for child in children.get(waiting.pop(), ()):
            if child not in included:
                included.add(child)
                waiting.append(child)
    return included


def _battlefield(state):
    return {
        oid: state.cards[oid]
        for seat in state.turn_order
        for oid in state.players[seat].zones["battlefield"]
    }


def _outgoing_phasing(host, battlefield, direct):
    if not direct:
        return {}
    children = _attachment_children(host, battlefield)
    outgoing = _expand_groups(direct, children)
    return {
        oid: PhaseTransition(
            _identity(battlefield[oid]),
            _record_outgoing(battlefield[oid], battlefield, host.state.players, outgoing, children),
            True,
        )
        for oid in outgoing
    }


def _source_control_prevents_phase_in(state, record):
    restriction = record.source_control_restriction
    return restriction is not None and controlled_source_duration_is_current(
        state, restriction.source, restriction.continuity, restriction.controller,
    )


def plan_untap_phasing(host, active: str) -> PhasingPlan:
    state = host.state
    battlefield = _battlefield(state)
    incoming, incoming_children, direct_out = set(), {}, set()
    for card in battlefield.values():
        record = card.phasing_record
        if card.phased_out:
            if record is None:
                if card.controller == active:
                    return PhasingPlan(active, unsupported=card.object_id)
                continue
            if record.return_seat not in host.state.players or (
                record.departed_ready and host.state.players[record.return_seat].in_game
            ):
                raise ValueError("Phasing return schedule is malformed")
            if record.indirect:
                parent = _host_incarnation(battlefield, record.attached_object)
                if parent is not None:
                    incoming_children.setdefault(parent.object_id, []).append(card.object_id)
            elif (record.battlefield_departure_hold is None
                  and (record.return_seat == active or record.departed_ready)
                  and not _source_control_prevents_phase_in(state, record)):
                incoming.add(card.object_id)
        elif card.controller == active:
            keywords = {str(value).casefold() for value in host._effective_card_data(card).get("keywords", ())}
            if "phasing" in keywords:
                direct_out.add(card.object_id)
    if direct_out:
        unsupported = direct_out - current_printed_phasing_object_ids(host)
        if unsupported:
            source = min(unsupported, key=lambda oid: battlefield[oid].ref)
            return PhasingPlan(active, unsupported=source)
    outgoing = _outgoing_phasing(host, battlefield, direct_out)
    incoming = _expand_groups(incoming, incoming_children)
    ordered = sorted(incoming | outgoing.keys(), key=lambda oid: (battlefield[oid].ref, oid))
    transitions = tuple(
        outgoing[oid] if oid in outgoing else
        PhaseTransition(_identity(battlefield[oid]), battlefield[oid].phasing_record, False)
        for oid in ordered
    )
    return PhasingPlan(active, transitions)


def _restore_phase_attachment(host, card, record):
    cards, players = host.state.cards, host.state.players
    target = _host_incarnation(cards, record.attached_object)
    if target is not None and target.zone == "battlefield":
        attach_objects(cards, card, target, source_timestamp=card.zone_timestamp, players=players)
    elif record.attached_player in host.active_seats:
        attach_to_player(cards, players, card, record.attached_player, source_timestamp=card.zone_timestamp)
    else:
        detach_object(cards, card, players=players)


def _commit_phasing(host, transitions, *, tapped_returns=frozenset()):
    affected = []
    for transition in transitions:
        card = _host_incarnation(host.state.cards, transition.object)
        if (
            card is None or card.zone != "battlefield" or card.phased_out == transition.out
            or card.phasing_record != (None if transition.out else transition.record)
        ):
            raise ValueError("A phasing plan no longer matches its permanent")
        affected.append((card, transition))
    if not affected:
        return ()
    from .relative_power_target import pin_host_relative_power_source_departures
    from .target_source import pin_targeting_source_departures
    from .scalar_effect_amounts import pin_scalar_characteristic_departures
    from .rules.stack_damage_sources import pin_creature_power_damage_departures
    from .token_copy_references import pin_copy_characteristic_departures
    from .rules.entry_type_return import pin_entry_type_return_departures
    from .rules.entry_creature_type_mana import pin_entry_creature_type_departures
    from .rules.entrant_power_branch import pin_entrant_power_departures
    from .rules.entrant_name_absence import pin_entrant_names_before_departure
    from .rules.entry_control_exchange import pin_entry_exchange_departures
    from .continuous_effect_state import expire_counter_bound_attack_restrictions

    outgoing = tuple(card for card, transition in affected if transition.out)
    pin_entry_type_return_departures(host, outgoing, error_type=ValueError)
    pin_entry_creature_type_departures(host, outgoing, error_type=ValueError)
    pin_entrant_power_departures(host, outgoing, error_type=ValueError)
    pin_entrant_names_before_departure(host, outgoing, error_type=ValueError)
    pin_entry_exchange_departures(host, outgoing, error_type=ValueError)
    pin_host_relative_power_source_departures(host, outgoing, phase_out=True)
    pin_targeting_source_departures(host, outgoing, error_type=ValueError, phase_out=True)
    pin_scalar_characteristic_departures(host, outgoing, error_type=ValueError, phase_out=True)
    pin_creature_power_damage_departures(host, outgoing, error_type=ValueError, phase_out=True)
    pin_copy_characteristic_departures(host, outgoing, error_type=ValueError, phase_out=True)
    # Commit the entire status batch before recalculating any live observers.
    for card, transition in affected:
        card.phased_out = transition.out
        card.phasing_record = transition.record if transition.out else None
        if transition.out:
            expire_counter_bound_attack_restrictions(host.state, card)
    for card, transition in affected:
        if not transition.out:
            if card.object_id in tapped_returns:
                from .tap_state import set_permanent_tapped

                set_permanent_tapped(host, card.ref, actor=card.controller, tapped=True,
                    reason="phased in tapped", logical_object_id=card.logical_object_id, log=False, semantic_events=False)
            _restore_phase_attachment(host, card, transition.record)
        record_source_transition(host, card, previous_phased_out=not transition.out)
    from .soulbond_state import end_pairs_containing

    end_pairs_containing(host, tuple(card.object_id for card, transition in affected if transition.out),
                         reason="paired object phased out")
    synchronize_control_effects(host, reason="phasing status changed")
    return tuple(affected)


def commit_untap_phasing(host, plan: PhasingPlan) -> tuple[ContinuousObjectIdentity, ...]:
    if plan.unsupported is not None or plan.active != host.state.active_player:
        raise ValueError("Phasing requires one represented current untap plan")
    affected = _commit_phasing(host, plan.transitions)
    if not affected:
        return ()
    host._log(plan.active, "untap.phasing", "Permanents phased in and out.",
        {"in": [card.ref for card, transition in affected if not transition.out],
         "out": [card.ref for card, transition in affected if transition.out]},
        importance=1, changed_objects=[card.object_id for card, _ in affected])
    return tuple(transition.object for _, transition in affected if not transition.out)


def phase_out_permanents(host, identities: tuple[ContinuousObjectIdentity, ...], *, actor: str, reason: str,
                         departure_hold: ContinuousObjectIdentity | None = None, linked_phase_in_tapped: bool = True):
    from .phasing_model import phasing_lifecycle_version

    if phasing_lifecycle_version(host.state.phasing_lifecycle_version) is None:
        raise ValueError("A phase-out effect requires the current phasing lifecycle")
    host._require_seat(actor, in_game=True)
    battlefield = _battlefield(host.state)
    direct = set()
    for identity in identities:
        card = _host_incarnation(battlefield, identity)
        if card is not None and not card.phased_out:
            direct.add(card.object_id)
    if not direct:
        return ()
    outgoing = _outgoing_phasing(host, battlefield, direct)
    if departure_hold is not None:
        for oid in direct:
            transition = outgoing[oid]
            if not transition.record.indirect:
                outgoing[oid] = replace(transition, record=replace(transition.record,
                    battlefield_departure_hold=departure_hold, linked_phase_in_tapped=linked_phase_in_tapped))
    ordered = sorted(outgoing, key=lambda oid: (battlefield[oid].ref, oid))
    affected = _commit_phasing(host, tuple(outgoing[oid] for oid in ordered))
    for permanent, _transition in affected:
        host._remove_object_from_combat(permanent, reason="phased out")
    host._log(
        actor, "permanent.phase_out", "Permanents phased out.",
        {"out": [permanent.ref for permanent, _transition in affected], "reason": reason},
        importance=1, changed_objects=[permanent.object_id for permanent, _transition in affected],
    )
    return tuple(battlefield[oid].ref for oid in ordered if oid in direct)


def phase_out_permanent(host, identity: ContinuousObjectIdentity, *, actor: str, reason: str):
    phased = phase_out_permanents(host, (identity,), actor=actor, reason=reason)
    return phased[0] if phased else None


def _capture_source_control_restriction(host, effect, *, actor, subject):
    from .continuous_effect_state import ResolutionEffectSource
    from .errors import GameRuleError

    source = ResolutionEffectSource.from_effect(effect)
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True or source.object_id == subject.object_id):
        raise GameRuleError("Source-controlled phasing requires its original resolving trigger and other target")
    facts = effect.get(RUNTIME_SOURCE_CONTINUITY_FIELD)
    if (not isinstance(facts, Mapping) or set(facts) != {"history", "resolution_timestamp"}
            or type(facts["resolution_timestamp"]) is not int or facts["resolution_timestamp"] < 0):
        raise GameRuleError("Source-controlled phasing requires its sealed resolution continuity")
    if source.object_id is None:
        return None
    identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    raw_history = facts["history"]
    history = SourceContinuitySnapshot.from_dict(raw_history) if raw_history is not None else None
    active = prepare_controlled_source_untap_duration(
        host.state, identity, history, facts["resolution_timestamp"], actor,
    )
    return SourceControlPhaseInRestriction(identity, actor, active) if active is not None else None


def _phase_out_controlled_set(host, *, controller, actor, reason, types=(), excluded_types=()):
    from .affected_permanents import (
        AffectedPermanentSetError, AffectedPermanentSetSpec, PermanentControllerRelation,
        select_affected_permanents,
    )
    from .errors import GameRuleError
    from .object_predicate import ObjectQuerySpec

    spec = AffectedPermanentSetSpec(ObjectQuerySpec(zones=("battlefield",), types_all=types, excluded_types=excluded_types),
                                    PermanentControllerRelation.ACTOR)
    try:
        permanents = select_affected_permanents(
            host.affected_permanent_object_rows(controller), spec, actor=controller,
            active_seats=host.affected_permanent_active_seats(), apnap_order=host.affected_permanent_apnap_order(),
        )
    except AffectedPermanentSetError as exc:
        raise GameRuleError(str(exc)) from exc
    return phase_out_permanents(host, tuple(_identity(card) for card in permanents), actor=actor, reason=reason)


def apply_phase_out_effect(host, effect, *, actor, operation, reason):
    from .continuous_effect_state import ResolutionEffectSource
    from .errors import GameRuleError
    from .phasing_model import (
        CONTROLLED_LAND_PHASE_OUT_SUBJECT, CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT,
        BATTLEFIELD_PHASE_OUT_SUBJECT, CREATURE_SET_PHASE_HOLD_SUBJECT, PHASE_OUT_TARGET_SET_SUBJECT, PROLIFERATE_PHASE_OUT_SUBJECT,
        SPELL_TARGET_PHASE_OUT_SUBJECT, TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT, validate_phase_out_effect,
    )
    from .rules.proliferate_phase_out import phase_out_selected_counter_recipients

    subject = validate_phase_out_effect(effect, runtime=True)
    from .rules.battlefield_phase_hold import battlefield_phase_hold_ticket

    if subject == BATTLEFIELD_PHASE_OUT_SUBJECT:
        ticket = battlefield_phase_hold_ticket(host, effect, actor=actor)
        if ticket is None:
            return None
        target, source = ticket
        result = phase_out_permanents(host, (_identity(target),), actor=actor, reason=reason, departure_hold=source)
        return result[0] if result else None
    if subject == CREATURE_SET_PHASE_HOLD_SUBJECT:
        from .rules.counted_creature_phase_hold import apply_counted_creature_phase_hold

        return apply_counted_creature_phase_hold(host, effect, actor=actor, reason=reason)
    if subject == SPELL_TARGET_PHASE_OUT_SUBJECT:
        from .rules.phase_occurrence_events import phase_out_original_spell_target

        return phase_out_original_spell_target(host, actor=actor, reason=reason)
    if subject == PROLIFERATE_PHASE_OUT_SUBJECT:
        return phase_out_selected_counter_recipients(host, effect, actor=actor, reason=reason)
    if subject in {CONTROLLED_LAND_PHASE_OUT_SUBJECT, CONTROLLED_PERMANENT_PHASE_OUT_SUBJECT}:
        types = ("land",) if subject == CONTROLLED_LAND_PHASE_OUT_SUBJECT else ()
        return _phase_out_controlled_set(host, controller=actor, actor=actor, reason=reason, types=types)
    if subject == TARGET_PLAYER_NONLAND_PHASE_OUT_SUBJECT:
        from .rules.targeted_opponent_protection import validate_resolving_opponent_protection

        try:
            controller = validate_resolving_opponent_protection(host, effect, actor=actor)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        return _phase_out_controlled_set(host, controller=controller, actor=actor, reason=reason, excluded_types=("land",))
    if subject == PHASE_OUT_TARGET_SET_SUBJECT:
        targets = []
        for reference in effect["cards"]:
            try:
                card = host._resolve_object(actor, reference, zones={"battlefield"})
            except GameRuleError:
                continue
            targets.append(_identity(card))
        return phase_out_permanents(host, tuple(targets), actor=actor, reason=reason)
    if subject == "source":
        source = ResolutionEffectSource.from_effect(effect)
        card = host.state.cards.get(source.object_id)
        if card is None or card.ref != source.card_ref or card.logical_object_id != source.logical_object_id:
            return None
    else:
        try:
            card = host._resolve_object(actor, effect["card"], zones={"battlefield"})
        except GameRuleError:
            return None
    result = phase_out_permanent(host, _identity(card), actor=actor, reason=reason)
    if subject == SOURCE_CONTROL_PHASE_OUT_SUBJECT and result is not None:
        restriction = _capture_source_control_restriction(host, effect, actor=actor, subject=card)
        if restriction is not None:
            card.phasing_record = replace(card.phasing_record, source_control_restriction=restriction)
    return result


def return_held_phasing_after_departures(host, departures):
    """Release all roots for the same actual battlefield-departure batch."""
    if not departures or getattr(host.state, "phasing_lifecycle_version", None) is None:
        return
    battlefield = _battlefield(host.state)
    from .conditional_exile_model import conditional_source_remains
    from .rules.phase_occurrence_events import collect_phase_in_triggers
    from .trigger_processing import enqueue_trigger_batch

    roots, children = set(), {}
    for oid, card in battlefield.items():
        record = card.phasing_record
        if not card.phased_out or record is None:
            continue
        if record.battlefield_departure_hold in departures and not conditional_source_remains(
            record.battlefield_departure_hold, battlefield,
        ):
            roots.add(oid)
        if record.indirect:
            attached = _host_incarnation(battlefield, record.attached_object)
            if attached is not None:
                children.setdefault(attached.object_id, []).append(oid)
    resulting = _expand_groups(roots, children)
    transitions = tuple(PhaseTransition(_identity(battlefield[oid]), battlefield[oid].phasing_record, False)
                        for oid in sorted(resulting, key=lambda oid: (battlefield[oid].ref, oid)))
    tapped = frozenset(oid for oid in roots if battlefield[oid].phasing_record.linked_phase_in_tapped)
    affected = _commit_phasing(host, transitions, tapped_returns=tapped)
    if not affected:
        return
    host._log(None, "permanent.phase_in", "Held permanents phased in after their source left.",
        {"in": [card.ref for card, _ in affected]}, importance=1, changed_objects=[card.object_id for card, _ in affected])
    enqueue_trigger_batch(host, collect_phase_in_triggers(host, tuple(t.object for _, t in affected)))


def mark_departed_phasing_returns(state: GameState, previous_normal: str | None, next_normal: str) -> None:
    """Cross departed seats only in the ordinary turn rotation."""
    if not state.eliminated_players or previous_normal is None or state.phasing_lifecycle_version is None:
        return
    order = tuple(state.turn_order)
    start = order.index(previous_normal)
    skipped = set()
    for offset in range(1, len(order) + 1):
        seat = order[(start + offset) % len(order)]
        if seat == next_normal:
            break
        if state.players[seat].in_game:
            return
        skipped.add(seat)
    if not skipped:
        return
    for card in state.cards.values():
        record = card.phasing_record
        if (record is not None and not record.indirect and record.battlefield_departure_hold is None
                and record.return_seat in skipped and not record.departed_ready):
            card.phasing_record = replace(record, departed_ready=True)
