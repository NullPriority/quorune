from __future__ import annotations

"""Schedule and resolve one opposing death's exact graveyard arrivals."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..characteristic_evaluation import type_parts
from ..continuous_effect_state import ResolutionEffectSource
from ..effect_contracts import REANIMATE_OPERATION
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..replacement.immutable import FrozenMap
from .death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS, decode_death_battlefield_arrivals


OPPOSING_DEATH_DELAYED_RETURN_VERSION = 21
OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY = "zone.return.opposing_death_delayed_counter_characteristics"
OPPOSING_DEATH_DELAYED_RETURN_MECHANIC = "opposing-death-delayed-counter-characteristic-return"
OPPOSING_DEATH_OBSERVER_CONTROLLER = "opposing_death_observer_controller"
OPPOSING_DEATH_DELAYED_RETURN_DEPENDENCIES = (
    OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY,
    "attachment.aura.typed_restriction",
    "counter.placement.quantity_replacement",
    "counter.producer.effect_entry",
    "trigger.delayed.fixed_object_action",
    "trigger.effect.fixed_event",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
    "zone.change.destination_replacement",
)
_END_STEP = {"phase": "ending", "step": "end_step"}
_RUNTIME_METADATA = frozenset({"_runtime_source", "reason"})
_ENTRY_SELECTIONS = frozenset({"_replacement_selections", "_aura_target_refs"})


def opposing_creature_death_condition():
    return {"field": "controller", "op": "ne", "value": "$source.controller"}


def opposing_death_delayed_return_effect():
    return {
        "op": REANIMATE_OPERATION,
        "schema_version": OPPOSING_DEATH_DELAYED_RETURN_VERSION,
        "phase": "schedule",
        DEATH_BATTLEFIELD_ARRIVALS: "$context." + DEATH_BATTLEFIELD_ARRIVALS,
        "controller": "$controller",
    }


def is_opposing_death_delayed_return(effect):
    return effect.get("op") == REANIMATE_OPERATION and (
        effect.get("schema_version") == OPPOSING_DEATH_DELAYED_RETURN_VERSION
        or DEATH_BATTLEFIELD_ARRIVALS in effect and "phase" in effect
    )


def opposing_death_delayed_return_node_capabilities(effects, target_schema):
    if target_schema is None and tuple(effects) == (opposing_death_delayed_return_effect(),):
        if type(effects[0]["schema_version"]) is int:
            return OPPOSING_DEATH_DELAYED_RETURN_DEPENDENCIES
    return ()


def opposing_death_delayed_return_runtime_dependencies(program):
    required = opposing_death_delayed_return_node_capabilities(program.effects, program.target_schema)
    if (
        not required or program.event != "creature.dies" or program.active_zone != "battlefield"
        or program.cost_schema is not None or program.handlers
        or FrozenMap(program.event_condition or {}) != FrozenMap(opposing_creature_death_condition())
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Delayed creature return requires the exact opposing battlefield death trigger")
    return required


def validate_opposing_death_delayed_return(effect, *, required_phase=None):
    """Read a materialized occurrence or a saved return continuation."""
    if not isinstance(effect, Mapping):
        raise ValueError("Opposing death continuation requires an instruction object")
    fields = opposing_death_delayed_return_effect().keys()
    phase = effect.get("phase")
    extras = _RUNTIME_METADATA | (_ENTRY_SELECTIONS if phase == "return" else frozenset())
    controller = effect.get("controller")
    if (
        not fields <= effect.keys() <= fields | extras or phase not in ("schedule", "return")
        or required_phase is not None and phase != required_phase
        or effect["op"] != REANIMATE_OPERATION or type(effect["schema_version"]) is not int
        or effect["schema_version"] != OPPOSING_DEATH_DELAYED_RETURN_VERSION
        or type(controller) is not str or not controller or controller.startswith("$")
        or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))
    ):
        raise ValueError("Opposing death continuation has a malformed resolving instruction")
    arrivals = decode_death_battlefield_arrivals(effect[DEATH_BATTLEFIELD_ARRIVALS])
    aura_targets = effect.get("_aura_target_refs", {})
    if (
        not isinstance(aura_targets, Mapping) or not set(aura_targets) <= {row.object_id for row in arrivals}
        or any(type(ref) is not str or not ref for ref in aura_targets.values())
    ):
        raise ValueError("Opposing death Aura choices require their original arrivals")
    return arrivals


def _delayed_return_effect(arrivals):
    return {
        **opposing_death_delayed_return_effect(),
        "phase": "return",
        DEATH_BATTLEFIELD_ARRIVALS: [row.to_dict() for row in arrivals],
    }


def opposing_death_observer_context(source, program):
    if OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY not in program.capability_dependencies:
        return {}
    return {OPPOSING_DEATH_OBSERVER_CONTROLLER: source.controller}


def _sealed_death_source(host, item, arrivals):
    facts = item.context
    former = facts.get("previous_controller")
    observer = facts.get(OPPOSING_DEATH_OBSERVER_CONTROLLER)
    copied_from = facts.get("copied_from_stack")
    program = host.semantics.get(item.semantic_key)
    if (
        facts.get("event") != "creature.dies" or facts.get("from") != "battlefield"
        or facts.get("to") != "graveyard" or "creature" not in facts.get("types", ())
        or type(former) is not str or not former or facts.get("controller") != former
        or type(observer) is not str or not observer or observer == former
        or observer != item.controller and (type(copied_from) is not str or not copied_from)
        or program is None or OPPOSING_DEATH_DELAYED_RETURN_CAPABILITY not in program.capability_dependencies
        or decode_death_battlefield_arrivals(facts.get(DEATH_BATTLEFIELD_ARRIVALS)) != arrivals
    ):
        raise ValueError("Delayed creature return lost the original death occurrence")
    opposing_death_delayed_return_runtime_dependencies(program)
    if facts.get("composition") is None and arrivals:
        arrival, = arrivals
        if (
            facts.get("token") is not False or facts.get("card") != arrival.reference
            or facts.get("card_zone_change_counter") != arrival.zone_change_counter
            or facts.get("owner") != arrival.owner
        ):
            raise ValueError("Delayed creature return has a different physical graveyard arrival")


def _schedule_opposing_return(host, item, actor, arrivals):
    from ..trigger_processing import schedule_delayed_trigger

    label = "Return the card from the original death"
    return schedule_delayed_trigger(
        host, controller=actor, label=label, event_kind="step.begin", condition=_END_STEP,
        stack_template={"context": {"dynamic_effects": [_delayed_return_effect(arrivals)]}},
        source_object_id=item.source_object_id, source_logical_object_id=item.context["source_logical_object_id"],
        referred_object_ids=[row.object_id for row in arrivals], once=True,
    ).ref


def _require_delayed_return_instruction(item, arrivals):
    original = item.context.get("dynamic_effects", ())
    if (
        item.context.get("event") != "step.begin" or not item.context.get("delayed_trigger_ref")
        or not isinstance(original, (list, tuple)) or len(original) != 1
        or not isinstance(original[0], Mapping) or FrozenMap(original[0]) != FrozenMap(_delayed_return_effect(arrivals))
    ):
        raise ValueError("Opposing death return lost its sealed end-step instruction")


def _return_eligible_arrivals(host, effect, source, actor, reason, arrivals):
    from .opposing_death_arrival import commit_opposing_death_return

    cards = tuple(
        card for row in arrivals
        if row.matches(card := host.state.cards.get(row.object_id)) and card.owner in host.active_seats
        and type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0] & PERMANENT_CARD_TYPES
    )
    if not cards:
        return ()
    return commit_opposing_death_return(
        host, cards, actor=actor, runtime_source=source, reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
        aura_target_refs=effect.get("_aura_target_refs"),
    )


def apply_opposing_death_delayed_return(host, effect, *, actor, reason):
    try:
        arrivals = validate_opposing_death_delayed_return(effect)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
        if (
            item is None or item.kind != "triggered_ability" or actor not in host.active_seats
            or item.controller != actor or effect["controller"] != actor
            or item.context.get("currently_resolving") is not True
            or not item.source_object_id or not item.context.get("source_logical_object_id")
            or source.object_id is not None and (source.object_id != item.source_object_id
                or source.logical_object_id != item.context["source_logical_object_id"])
        ):
            raise ValueError("Opposing death return requires its resolving trigger and controller")
        scheduling = effect["phase"] == "schedule"
        if scheduling:
            _sealed_death_source(host, item, arrivals)
        else:
            _require_delayed_return_instruction(item, arrivals)
    except (KeyError, TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    if scheduling:
        return _schedule_opposing_return(host, item, actor, arrivals)
    return _return_eligible_arrivals(host, effect, source, actor, reason, arrivals)


def reanimation_death_occurrence_owner(effect):
    from .death_battlefield_return import apply_death_battlefield_return, is_death_battlefield_return

    if is_opposing_death_delayed_return(effect):
        return apply_opposing_death_delayed_return
    if is_death_battlefield_return(effect):
        return apply_death_battlefield_return
    return None
