from __future__ import annotations

"""One exact targeted phasing hold ending with the original source's actual departure."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..phasing_model import PHASE_OUT_CAPABILITY, PHASING_CAPABILITY, validate_battlefield_phase_out
from ..replacement.immutable import FrozenMap
from .phase_occurrence_model import PHASE_IN_CAPABILITY


BATTLEFIELD_PHASE_HOLD_CAPABILITY = "permanent.phasing.until_battlefield_departure"
BATTLEFIELD_PHASE_HOLD_MECHANIC = "fixed-phase-out-until-battlefield-departure"
BATTLEFIELD_PHASE_HOLD_SCHEMA = FrozenMap({
    "zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"],
})


def battlefield_phase_hold_capabilities(effect, target_schema):
    if (not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
            or FrozenMap(target_schema) != BATTLEFIELD_PHASE_HOLD_SCHEMA):
        return ()
    try:
        validate_battlefield_phase_out(effect)
    except (TypeError, ValueError):
        return ()
    return (BATTLEFIELD_PHASE_HOLD_CAPABILITY, PHASING_CAPABILITY, PHASE_OUT_CAPABILITY, PHASE_IN_CAPABILITY,
            "target.revalidate_resolution", "trigger.event.normalized_zone_change", "trigger.effect.fixed_event",
            "trigger.placement.apnap")


def battlefield_phase_hold_runtime_dependencies(program):
    required = (battlefield_phase_hold_capabilities(program.effects[0], program.target_schema)
                if len(program.effects) == 1 else ())
    if (not required or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.handlers or program.cost_schema is not None or program.event_condition is not None):
        raise ValueError("A battlefield phase hold requires its original complete entry trigger")
    return required


def admitted_phase_hold_source(host, effect, *, actor):
    origin = ResolutionEffectSource.from_effect(effect)
    item = next((candidate for candidate in host.state.stack if candidate.ref == origin.stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True
            or item.source_object_id != origin.object_id):
        raise ValueError("A battlefield phase hold requires its original resolving entry trigger")
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("A battlefield phase hold requires its admitted entry program")
    return item, program, ContinuousObjectIdentity(origin.object_id, origin.logical_object_id)


def battlefield_phase_hold_ticket(host, effect, *, actor):
    from ..conditional_exile_model import conditional_source_remains

    try:
        item, program, source = admitted_phase_hold_source(host, effect, actor=actor)
        if (tuple(item.targets) != (effect["card"],)
                or not set(battlefield_phase_hold_runtime_dependencies(program)) <= set(program.capability_dependencies)):
            raise ValueError("A battlefield phase hold requires its original targeted entry program")
        if not conditional_source_remains(source, host.state.cards):
            return None
        card = host._resolve_object(actor, effect["card"], zones={"battlefield"})
        snapshot = (item.context.get("target_snapshots") or {}).get(card.ref, {})
        data = host._effective_card_data(card)
        if (snapshot.get("object_id") != card.object_id or snapshot.get("zone_change_counter") != card.zone_change_counter
                or card.phased_out or "creature" not in host._type_parts(str(data.get("type_line") or ""))[0]):
            raise ValueError("A battlefield phase hold requires its original current creature")
        return card, source
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
