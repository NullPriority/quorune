from __future__ import annotations

"""Untap, commit a linked phase-out batch, and queue its actual count for placement."""

from ..affected_permanents import AffectedPermanentSetSpec, select_affected_permanents
from ..conditional_exile_model import conditional_source_remains
from ..continuous_effect_model import ContinuousObjectIdentity
from ..control_effects import synchronize_control_effects
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..phasing_model import PHASE_OUT_CAPABILITY, PHASING_CAPABILITY, creature_set_phase_hold_effect
from ..public_tap_state_sets import commit_public_tap_state_snapshot, snapshot_public_tap_state
from ..replacement.immutable import FrozenMap
from .battlefield_phase_hold import BATTLEFIELD_PHASE_HOLD_CAPABILITY, admitted_phase_hold_source
from .committed_quantity_followups import CommittedQuantityFollowup


COUNTED_CREATURE_PHASE_HOLD_CAPABILITY = "permanent.phasing.untap_creature_set_until_battlefield_departure"
COUNTED_CREATURE_PHASE_HOLD_MECHANIC = "fixed-untap-and-counted-creature-phasing-hold"
DEPENDENCIES = (
    COUNTED_CREATURE_PHASE_HOLD_CAPABILITY, BATTLEFIELD_PHASE_HOLD_CAPABILITY, PHASE_OUT_CAPABILITY, PHASING_CAPABILITY,
    "permanent.tap_state.fixed_set",
    "counter.producer.fixed_effect", "counter.placement.quantity_replacement", "trigger.event.normalized_zone_change",
    "trigger.effect.fixed_event", "trigger.placement.apnap",
)
_CREATURES = AffectedPermanentSetSpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))


def counted_creature_phase_hold_capabilities(effect, target_schema):
    if (target_schema is not None or type(effect.get("schema_version")) is not int
            or FrozenMap(effect) != FrozenMap(creature_set_phase_hold_effect())):
        return ()
    return DEPENDENCIES


def counted_creature_phase_hold_runtime_dependencies(program):
    required = (counted_creature_phase_hold_capabilities(program.effects[0], program.target_schema)
                if len(program.effects) == 1 else ())
    if (not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("Counted creature phasing requires its exact untargeted entry trigger")
    return required


def apply_counted_creature_phase_hold(host, effect, *, actor, reason):
    from ..phasing import phase_out_permanents

    try:
        item, program, source = admitted_phase_hold_source(host, effect, actor=actor)
        if item.targets or not set(counted_creature_phase_hold_runtime_dependencies(program)) <= set(program.capability_dependencies):
            raise ValueError("Counted creature phasing requires its admitted untargeted entry program")
        rows = select_affected_permanents(
            host.affected_permanent_object_rows(actor), _CREATURES, actor=actor,
            active_seats=host.affected_permanent_active_seats(), apnap_order=host.affected_permanent_apnap_order(),
        )
        snapshot = snapshot_public_tap_state(rows)
        commit_public_tap_state_snapshot(host, snapshot, actor=actor, tapped=False, reason=reason)
        synchronize_control_effects(host, reason="creature set untapped")
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if not conditional_source_remains(source, host.state.cards):
        return CommittedQuantityFollowup(())
    originals = tuple(ContinuousObjectIdentity(row.object_id, row.logical_object_id) for row in snapshot)
    phased = phase_out_permanents(host, originals, actor=actor, reason=reason,
                                 departure_hold=source, linked_phase_in_tapped=False)
    current = host.state.cards.get(source.object_id)
    if not phased or not conditional_source_remains(source, host.state.cards) or current.phased_out:
        return CommittedQuantityFollowup(())
    return CommittedQuantityFollowup((FrozenMap({
        "op": "place_counters", "card": "$source.zone_object", "counter": "time", "amount": len(phased), "source": "$source",
    }),))
