"""Collect one optional counter payment per player and protect the trigger controller."""

from collections.abc import Mapping

from ..counter_placement import (
    CounterPlacementError, CounterPlacementRequest, commit_prepared_counter_placements,
    prepare_counter_placements,
)
from ..errors import GameRuleError
from ..next_turn_attack_restriction_model import UNTIL_PROTECTED_TURN_CAPABILITY
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .cumulative_object_upkeep import CONTROLLER_UPKEEP
from .effect_shape_walkers import nested_effects
from .next_turn_attack_restrictions import apply_until_protected_turn_attack_restrictions


FIELD = "apnap_counter_attack_truce_version"
INCARNATIONS = "apnap_counter_attack_truce_incarnations"
MECHANIC = "apnap-counter-payment-attack-truce"
CAPABILITY = "choice.apnap.optional_counter_attack_truce"
COMMIT = "commit_apnap_counter_attack_truce"


def optional_counter_attack_truce_effect(actor="$controller"):
    return {
        "op": "choose_cards_apnap", "actor": actor, "players": "all",
        "zone": "battlefield", "count": 1, "then": "counter_attack_truce",
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
        "prompt": "Choose a creature you control to receive two +1/+1 counters, or decline.",
        FIELD: 1,
    }


def counter_attack_truce_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect, = effects
    if (type(effect.get(FIELD)) is not int or type(effect.get("count")) is not int
            or FrozenMap(effect) != FrozenMap(optional_counter_attack_truce_effect())):
        return ()
    return (CAPABILITY, UNTIL_PROTECTED_TURN_CAPABILITY, "trigger.effect.fixed_event", "trigger.placement.apnap")


def counter_attack_truce_program_dependencies(program):
    if not (MECHANIC in program.coverage or any(FIELD in effect or effect["op"] == COMMIT
            for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    required = counter_attack_truce_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or program.handlers or program.cost_schema is not None
            or FrozenMap(program.event_condition or {}) != FrozenMap(CONTROLLER_UPKEEP)):
        raise ValueError("Counter-payment truce requires its complete untargeted upkeep trigger")
    return required


def is_closed_counter_attack_truce_program(program):
    try:
        required = counter_attack_truce_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def require_resolving_counter_attack_truce(host, stack_ref):
    item = next((candidate for candidate in host.state.stack if candidate.ref == stack_ref), None)
    if (item is None or item.kind != "triggered_ability" or item.targets or item.controller not in host.active_seats
            or item.context.get("currently_resolving") is not True
            or (item.context.get("event"), item.context.get("step")) != ("step.begin", "upkeep")):
        raise GameRuleError("Counter-payment truce requires its resolving upkeep trigger")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_counter_attack_truce_program(program)):
        raise GameRuleError("Counter-payment truce requires its trusted complete program")
    return item


def validate_counter_attack_truce_choice(host, effect, continuation):
    item = require_resolving_counter_attack_truce(host, continuation.get("stack_ref"))
    if (type(effect.get("count")) is not int or type(effect.get(FIELD)) is not int
            or FrozenMap(effect) != FrozenMap(optional_counter_attack_truce_effect(item.controller))):
        raise GameRuleError("Counter-payment truce must retain its original choice scope")
    return item


def counter_attack_truce_commit_effect(host, state):
    effect = state["effect"]
    item = validate_counter_attack_truce_choice(host, effect, state["resume"])
    order = state["player_order"]
    selections = state["selected"]
    identities = state.get(INCARNATIONS, {})
    if order != host.apnap_order() or not isinstance(selections, Mapping) or set(selections) != set(order):
        raise GameRuleError("Counter-payment truce has incomplete APNAP choices")
    placements = []
    for seat in order:
        refs = selections[seat]
        if (not isinstance(refs, (tuple, list)) or len(refs) > 1
                or any(type(ref) is not str or ref not in host._choice_options(seat, effect) for ref in refs)):
            raise GameRuleError("Counter-payment selection is stale or malformed")
        if refs:
            ref, = refs
            placements.append({"player": seat, "ref": ref, "incarnation": identities.get(ref)})
    return {"op": COMMIT, "stack_ref": item.ref, "placements": placements}


def apply_counter_attack_truce(host, effect, *, actor, reason):
    required = {"op", "stack_ref", "placements"}
    if (not isinstance(effect, Mapping) or set(effect) - {"_replacement_selections", "_runtime_source"} != required
            or effect["op"] != COMMIT or not isinstance(effect["placements"], (list, tuple))):
        raise GameRuleError("Counter-payment truce commit has a closed schema")
    item = require_resolving_counter_attack_truce(host, effect["stack_ref"])
    if item.controller != actor:
        raise GameRuleError("Counter-payment truce controller changed")
    source_ref = host._stack_source_ref(item)
    requests = []
    event_ids = []
    accepting_players = []
    options = optional_counter_attack_truce_effect(actor)
    for placement in effect["placements"]:
        if (not isinstance(placement, Mapping) or set(placement) != {"player", "ref", "incarnation"}
                or any(type(value) is not str or not value for value in placement.values())):
            raise GameRuleError("Counter-payment truce placement is malformed")
        seat, ref = placement["player"], placement["ref"]
        if seat not in host.active_seats or seat in accepting_players or ref not in host._choice_options(seat, options):
            raise GameRuleError("Counter-payment truce requires one legal creature per accepting player")
        card = host._resolve_object(seat, ref, zones={"battlefield"}, controlled_only=True)
        if card.logical_object_id != placement["incarnation"]:
            raise GameRuleError("Counter-payment creature incarnation changed")
        accepting_players.append(seat)
        event_ids.append(f"counter.place:{item.stack_id}:apnap-truce:{seat}:{card.logical_object_id}")
        requests.append(CounterPlacementRequest(
            subject_kind="permanent", subject_id=card.object_id, placing_player=seat,
            counter_name="+1/+1", amount=2, source_ref=source_ref, effect_generated=False,
        ))
    if accepting_players != [seat for seat in host.apnap_order() if seat in accepting_players]:
        raise GameRuleError("Counter-payment truce placements lost their APNAP order")
    if not requests:
        return ()
    try:
        prepared = prepare_counter_placements(
            host, requests, selections=effect.get("_replacement_selections", ()), event_ids=event_ids, for_cost=True,
        )
        results = commit_prepared_counter_placements(host, prepared, reason=reason)
    except CounterPlacementError as exc:
        raise GameRuleError(str(exc)) from exc
    apply_until_protected_turn_attack_restrictions(
        host, source_id=item.ref, protected_player=actor, accepting_players=accepting_players,
    )
    return tuple(host.state.cards[result.object_id].ref for result in results)
