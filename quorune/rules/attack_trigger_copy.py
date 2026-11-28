"""A declared attack's own triggered ability and its dependent optional copy."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value
from ..resolution_conditions import RESOLUTION_CONDITION_OPERATION
from ..trigger_batches import SECONDARY_TRIGGER_CONTEXT
from .effect_shape_walkers import nested_effects
from .nonmana_activation_copy_model import ActivationCopySource, TRIGGER_COPY_SOURCE_VERSION


EVENT = "ability.triggered.by_declared_attack"
TEMPLATE = "declared-attacking-creature-ability-quest-copy-v1"
CAPABILITY = "stack.copy.declared_attack_trigger"
COPY_CONTEXT = "declared_attack_copy_source"
QUEST_CONTEXT = "declared_attack_copy_quest_observation"
RESULT_FIELD = "declared_attack_quest_copy"
DEPENDENCIES = (
    CAPABILITY, "counter.producer.fixed_effect", "counter.placement.quantity_replacement",
    "effect.choice.optional_fixed", "resolution.effect.public_condition", "stack.copy.fixed_qualified_target",
    "trigger.effect.fixed_event", "trigger.event.normalized_public_action", "trigger.placement.apnap",
)


def condition():
    return {"field": "player", "op": "eq", "value": "$source.controller"}


def effects():
    return (
        {"op": "place_counters", "card": "$source.zone_object", "counter": "quest",
         "amount": 1, "source": "$source"},
        {"op": RESOLUTION_CONDITION_OPERATION, "player": "$controller",
         RESULT_FIELD: {"schema_version": 1, "counter": "quest", "minimum": 4}},
    )


def uses_quest_copy(effect):
    return isinstance(effect, Mapping) and RESULT_FIELD in effect


def program_dependencies(program):
    if program.event != EVENT and not any(uses_quest_copy(effect) for effect in nested_effects(
            (program.effects, program.handlers))):
        return ()
    expected = effects()
    if ((program.event, program.active_zone) != (EVENT, "battlefield") or program.target_schema is not None
            or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
            or FrozenMap(program.event_condition or {}) != FrozenMap(condition())
            or thaw_value(program.effects) != thaw_value(expected)
            or type(program.effects[0]["amount"]) is not int
            or type(program.effects[1][RESULT_FIELD]["schema_version"]) is not int
            or type(program.effects[1][RESULT_FIELD]["minimum"]) is not int):
        raise ValueError("Attack-trigger copying requires the complete staged occurrence and quest result")
    return DEPENDENCIES


def _counter_snapshot_matches(card, observation):
    return (card is not None and card.zone == "battlefield" and card.ref == observation["reference"]
            and card.logical_object_id == observation["logical_object_id"])


def quest_observation(context):
    observation = context.get(QUEST_CONTEXT)
    if (not isinstance(observation, Mapping)
            or set(observation) != {"reference", "logical_object_id", "counter", "departure_count"}
            or observation["counter"] != "quest"
            or any(type(observation[key]) is not str or not observation[key]
                   for key in ("reference", "logical_object_id"))
            or observation["departure_count"] is not None and (
                type(observation["departure_count"]) is not int or observation["departure_count"] < 0)):
        raise ValueError("Attack-trigger copying requires the original quest counter subject")
    return observation, observation


def initial_counter_observation(source, current):
    observation = {"reference": source.ref, "logical_object_id": source.logical_object_id,
                   "counter": "quest", "departure_count": None}
    if not _counter_snapshot_matches(current, observation):
        observation["departure_count"] = source.counters.get("quest", 0)
    return {QUEST_CONTEXT: observation, "source_logical_object_id": source.logical_object_id}


def builtin_attack_source_identity(item):
    from ..attack_counter_triggers import ATTACK_COUNTER_TRIGGER_SEMANTIC_KEY
    from ..attack_transition_resolution import ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY
    from ..mentor import MENTOR_TRIGGER_SEMANTIC_KEY

    contexts = {
        ATTACK_COUNTER_TRIGGER_SEMANTIC_KEY: "attack_counter_trigger",
        ATTACK_KEYWORD_TRIGGER_SEMANTIC_KEY: "attack_keyword_trigger",
        MENTOR_TRIGGER_SEMANTIC_KEY: "mentor_trigger",
    }
    context_field = contexts.get(item.semantic_key)
    if context_field is None or item.context.get("event") != "combat.attack_transition":
        return None
    occurrence = item.context.get(context_field)
    return occurrence.get("transition_id") if isinstance(occurrence, Mapping) else None


def _caused_by_own_attack(host, item, event, attackers):
    if item.kind != "triggered_ability" or item.source_object_id not in attackers:
        return False
    card = host.state.cards[item.source_object_id]
    if item.controller != card.controller or item.controller != event.active_player:
        return False
    if builtin_attack_source_identity(item) == event.transition_id:
        return True
    program = host.semantics.get(item.semantic_key)
    context = item.context
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or context.get("event_id") != event.transition_id):
        return False

    from .attack_declaration_mana import EVENT as PLAYER_ATTACK_EVENT

    if (program.event, context.get("event")) == (PLAYER_ATTACK_EVENT, PLAYER_ATTACK_EVENT):
        declaration = context.get("attack_transition")
        return isinstance(declaration, Mapping) and FrozenMap(declaration) == FrozenMap(event.to_dict())
    return context.get("event") == "creature.attacks" and context.get("card") == card.ref


def append_attack_trigger_observers(host, event, items, sources):
    from .event_subscriptions import event_has_capability_subscriber
    from .nonmana_activation_copy import capture_activation_copy_source

    if not event_has_capability_subscriber(host, EVENT, CAPABILITY):
        return
    attackers = frozenset(assignment.attacker_object_id for assignment in event.assignments)
    for item in tuple(items):
        if _caused_by_own_attack(host, item, event, attackers):
            capture = capture_activation_copy_source(host, item, schema_version=TRIGGER_COPY_SOURCE_VERSION)
            start = len(items)
            host._dispatch_semantic_event(EVENT, {
                "event_id": item.stack_id, "player": item.controller, "stack": item.ref,
                COPY_CONTEXT: capture.to_dict(), SECONDARY_TRIGGER_CONTEXT: True,
            }, sources=sources, trigger_batch=items)
            for observer in items[start:]:
                observer.label = f"{observer.label} — {item.label} ({item.ref})"


def resolved_quest_copy(host, instruction, item):
    expected = effects()[1]
    if FrozenMap(instruction) != FrozenMap(expected):
        raise ValueError("Attack-trigger copying requires its exact quest instruction")
    context = item.context
    original = ActivationCopySource.from_dict(context.get(COPY_CONTEXT))
    observed, _ = quest_observation(context)
    if (original.schema_version != TRIGGER_COPY_SOURCE_VERSION or context.get("event") != EVENT
            or context.get(SECONDARY_TRIGGER_CONTEXT) is not True or original.logical_object_id != context.get("event_id")
            or original.item().ref != context.get("stack") or item.kind != "triggered_ability"):
        raise ValueError("Attack-trigger copying lost its causing occurrence")
    source = host.state.cards.get(item.source_object_id)
    count = source.counters.get("quest", 0) if _counter_snapshot_matches(source, observed) else observed["departure_count"]
    if type(count) is not int or count < 0:
        raise ValueError("Attack-trigger copying has no valid current or last-known quest count")
    original_available = original.departed or any(
        row.ref == original.item().ref for row in host.state.stack
    )
    return {**expected, "player": item.controller, "quest_count": count,
            COPY_CONTEXT: original.to_dict() if count >= 4 and original_available else None}


def quest_copy_results(effect, *, actor, stack_controller):
    expected = {**effects()[1], "player": actor}
    if (actor != stack_controller or set(effect) != {*expected, "quest_count", COPY_CONTEXT}
            or FrozenMap({key: effect[key] for key in expected}) != FrozenMap(expected)
            or type(effect["quest_count"]) is not int or effect["quest_count"] < 0):
        raise ValueError("Attack-trigger quest result lost its original controller or count")
    if effect[COPY_CONTEXT] is None:
        return ()
    source = ActivationCopySource.from_dict(effect[COPY_CONTEXT])
    if effect["quest_count"] < 4 or source.schema_version != TRIGGER_COPY_SOURCE_VERSION:
        raise ValueError("Attack-trigger copying requires four actual quest counters and one cause")
    return (FrozenMap({"op": "offer_optional_effect", "player": actor, "effects": [
        {"op": "copy_stack_item", "stack": source.item().ref, "copy_source": source.to_dict()},
    ]}),)
