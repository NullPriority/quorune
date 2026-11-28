"""Use one sealed creature selection in declaration and delayed destruction."""

from collections.abc import Mapping

from ..activation_condition_model import ActivationCondition, ActivationConditionKind
from ..chosen_attack_turn_model import (
    CAPABILITY, CHOOSE, COMMIT, DELAYED_CAPABILITY, DELAYED_FIELD, DESTROY, MECHANIC,
    ChosenAttackTurn, ResolvedChosenAttackTurn,
)
from ..combat_constraints import DeclarationRequirement
from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import commit_continuous_effect
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap
from ..trigger_processing import schedule_delayed_trigger
from ..turn_history import current_turn_history_events
from .effect_shape_walkers import nested_effects


TARGET = FrozenMap({"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1})
INSTRUCTION = FrozenMap({"op": CHOOSE, "schema_version": 1, "player": "$target.0"})
DEPENDENCIES = (
    CAPABILITY, DELAYED_CAPABILITY, "combat.declaration.typed_components",
    "target.revalidate_resolution", "trigger.placement.apnap",
)
_PRICE = FrozenMap({
    "text": "{T}", "mana": dict.fromkeys(("B", "C", "G", "GENERIC", "R", "U", "W"), 0),
    "complex_symbols": [], "tap_source": True, "untap_source": False,
    "discard_source": False, "sacrifice_source": False, "exile_source": False, "life_payment": 0,
    "energy_payment": 0, "loyalty_delta": None, "source_counter_removal_cost": None,
    "choices": [], "uncompiled_costs": [],
})


def chosen_attack_turn_activation_matches(ability):
    from ..compiler.activated_costs import activated_ability_cost

    return (
        ability.zones == ("battlefield",) and not ability.mana_ability and not ability.sorcery_speed
        and ability.activation_conditions == (ActivationCondition(ActivationConditionKind.CONTROLLERS_TURN),)
        and FrozenMap(activated_ability_cost(ability)) == _PRICE
    )


def chosen_attack_turn_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if (MECHANIC not in mechanics or len(effects) != 1 or FrozenMap(effects[0]) != INSTRUCTION
            or type(effects[0].get("schema_version")) is not int or target_schema is None
            or FrozenMap(target_schema) != TARGET or type(target_schema.get("count")) is not int
            or not isinstance(cost_schema, Mapping) or FrozenMap(cost_schema) != _PRICE
            or any(type(cost_schema[key]) is not type(_PRICE[key]) for key in
                   ("tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
                    "life_payment", "energy_payment"))
            or any(type(value) is not int for value in cost_schema["mana"].values())):
        return ()
    return DEPENDENCIES


def chosen_attack_turn_runtime_dependencies(program):
    if not (MECHANIC in program.coverage or any(effect["op"] in (CHOOSE, COMMIT, DESTROY)
            for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    required = chosen_attack_turn_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        cost_schema=program.cost_schema,
    )
    if (not required or (program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Chosen attackers need their closed targeted activation")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if (len(abilities) != 1 or not chosen_attack_turn_activation_matches(abilities[0])
            or program.ability_id != f"ability:{abilities[0].ability_id}"):
        raise ValueError("Chosen attackers require the original tap payment and controller turn")
    return required


def is_closed_chosen_attack_turn_program(program):
    try:
        required = chosen_attack_turn_runtime_dependencies(program)
    except (ValueError, TypeError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def current_chosen_attack_turns(state, player):
    return tuple(rule for rule in state.continuous_effects or ()
                 if isinstance(rule, ResolvedChosenAttackTurn) and rule.turn.is_current(state, player))


def chosen_attack_turn_prohibits(state, card, player):
    rules = current_chosen_attack_turns(state, player)
    if not rules:
        return False
    identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    return any(identity not in rule.turn.selected for rule in rules)


def chosen_attack_turn_requirements(state, card):
    rules = current_chosen_attack_turns(state, card.controller)
    if not rules:
        return ()
    identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    return tuple(DeclarationRequirement(
        requirement_id=f"attack:{card.ref}:chosen:{rule.effect_id}",
        kind="choose", variable=card.ref, label="This chosen creature attacks if able.",
    ) for rule in rules if identity in rule.turn.selected)


def apply_chosen_attack_turn(host, effect, *, actor):
    if (not isinstance(effect, Mapping) or set(effect) - {"_runtime_source"} != {"op", "stack_ref", "player", "selected"}
            or effect["op"] != COMMIT or not isinstance(effect["selected"], (list, tuple))):
        raise GameRuleError("Chosen attack turn result has a closed schema")
    item = next((value for value in host.state.stack if value.ref == effect["stack_ref"]), None)
    player = effect["player"]
    if (item is None or item.kind != "activated_ability" or item.controller != actor
            or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (player,)
            or player not in host.active_seats or player == actor):
        raise GameRuleError("Chosen attackers lost their original activated ability and opponent")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_chosen_attack_turn_program(program)):
        raise GameRuleError("Chosen attackers require their trusted selection program")
    try:
        turn = ChosenAttackTurn(player, host.state.players[player].turns_begun + 1, tuple(
            ContinuousObjectIdentity.from_dict(value) for value in effect["selected"]
        ))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    for identity in turn.selected:
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.logical_object_id != identity.logical_object_id or card.controller != player
                or card.zone != "battlefield" or card.phased_out
                or "creature" not in host._effective_type_parts(card)[0]):
            raise GameRuleError("A selected creature incarnation changed before commit")
    commit_continuous_effect(host.state, ResolvedChosenAttackTurn(
        host._next_ref("CE"), item.ref, host._next_zone_timestamp(), turn,
    ))
    source_identity = item.context.get("source_logical_object_id")
    payload = turn.to_dict()
    schedule_delayed_trigger(
        host, controller=actor, label="Destroy chosen nonattackers", event_kind="step.begin",
        condition={DELAYED_FIELD: payload},
        stack_template={"context": {
            DELAYED_FIELD: payload, "dynamic_effects": [{"op": DESTROY, "turn": payload}],
        }},
        source_object_id=item.source_object_id, source_logical_object_id=source_identity,
        referred_object_ids=[identity.object_id for identity in turn.selected],
    )


def chosen_attack_end_step_matches(state, trigger, context):
    if set(trigger.condition) != {DELAYED_FIELD} or not trigger.once:
        raise GameRuleError("The chosen attack delayed trigger changed its scope")
    turn = ChosenAttackTurn.from_dict(trigger.condition[DELAYED_FIELD])
    if turn.has_ended(state):
        return False
    if (turn.is_current(state, turn.player)
            and (context.get("player"), context.get("phase"), context.get("step")) ==
            (turn.player, "ending", "end_step")):
        return True
    return None


def resolve_chosen_nonattackers(host, item, effect):
    if (set(effect) != {"op", "turn"} or item.kind != "triggered_ability" or item.targets
            or item.context.get("event") != "step.begin" or not item.context.get("delayed_trigger_ref")
            or item.context.get(DELAYED_FIELD) != effect["turn"]):
        raise GameRuleError("Chosen nonattackers need their sealed delayed trigger")
    turn = ChosenAttackTurn.from_dict(effect["turn"])
    if not turn.is_current(host.state, turn.player) or host.state.step != "end_step":
        raise GameRuleError("The chosen creatures are checked in their originally scheduled end step")
    history = host.state.turn_history
    sequence = host.state.turn_sequence
    if history is None or history.turn_sequence != sequence:
        raise GameRuleError("The chosen turn's committed attack history is unavailable")
    attacked = {event.object_incarnation for event in current_turn_history_events(
        history, turn_sequence=sequence, kind="creature_attacked",
    )}
    refs = []
    for identity in turn.selected:
        card = host.state.cards.get(identity.object_id)
        if (card is not None and card.logical_object_id == identity.logical_object_id
                and card.zone == "battlefield" and not card.phased_out and identity.logical_object_id not in attacked):
            refs.append(card.ref)
    return {"op": "destroy_targets", "cards": refs, "maximum_targets": len(refs)} if refs else None
