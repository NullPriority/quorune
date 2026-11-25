"""Use the canonical source counter receipt to place its separate reflexive trigger."""

from ..counter_placement import CounterPlacementResult, place_counters_on_refs
from ..errors import GameRuleError
from ..reflexive_trigger_model import (
    BOUND_REFLEXIVE_PROGRAM_VERSION, REFLEXIVE_PAYMENT_CAPABILITY, REFLEXIVE_PAYMENT_MECHANIC,
    SOURCE_COUNTER_REFLEXIVE_FIELD, ReflexiveTriggerProgram,
)
from ..replacement.immutable import FrozenMap, thaw_value
from ..replacement.ordering import ReplacementChoiceRequired
from .effect_shape_walkers import contains_nested_key
from .source_optional_unblockable import source_optional_unblockable_node_capabilities


SOURCE_COUNTER_REFLEXIVE_CAPABILITY = "trigger.reflexive.committed_source_counter"
SOURCE_COUNTER_EFFECT = {"op": "place_counters", "card": "$source.zone_object",
                         "counter": "+1/+1", "amount": 1, "source": "$source"}
SOURCE_COUNTER_REFLEXIVE_MECHANICS = ("cr-122-counters", REFLEXIVE_PAYMENT_MECHANIC)


def source_counter_reflexive_condition():
    return {"all": [
        {"all": [{"field": "player", "op": "eq", "value": "$source.controller"},
                 {"field": "step", "op": "eq", "value": "beginning_combat"}]},
        {"field": "fixed_public_state_condition", "op": "truthy", "value": True,
         "condition": {"schema_version": 1, "kind": "controller_noncreature_spell_cast_count_at_least",
                       "amount": 1, "counter_name": None}},
    ]}


def source_counter_reflexive_child(value):
    from .source_optional_unblockable import source_optional_unblockable_target_schema

    child = ReflexiveTriggerProgram.from_dict(value)
    schema = thaw_value(child.target_schema)
    owned = source_optional_unblockable_node_capabilities(
        effects=child.effects, target_schema=schema, mechanic_ids=child.mechanic_ids,
    )
    if (child.schema_version != BOUND_REFLEXIVE_PROGRAM_VERSION or not owned
            or schema != source_optional_unblockable_target_schema(controlled=True)):
        raise ValueError("The source counter needs its exact other controlled creature consequence")
    return child, owned


def source_counter_reflexive_node_capabilities(*, effects, target_schema, mechanic_ids):
    if (len(effects) != 1 or target_schema is not None
            or not set(SOURCE_COUNTER_REFLEXIVE_MECHANICS) <= set(mechanic_ids)):
        return ()
    effect = thaw_value(effects[0])
    if SOURCE_COUNTER_REFLEXIVE_FIELD not in effect:
        return ()
    child_spec = effect[SOURCE_COUNTER_REFLEXIVE_FIELD]
    counter_effect = {key: value for key, value in effect.items() if key != SOURCE_COUNTER_REFLEXIVE_FIELD}
    if counter_effect != SOURCE_COUNTER_EFFECT or type(counter_effect.get("amount")) is not int:
        return ()
    try:
        _, owned = source_counter_reflexive_child(child_spec)
    except (TypeError, ValueError, KeyError):
        return ()
    return (SOURCE_COUNTER_REFLEXIVE_CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY,
            "counter.producer.fixed_effect", "trigger.placement.apnap", *owned)


def source_counter_reflexive_runtime_dependencies(program):
    if not contains_nested_key((*program.effects, *getattr(program, "handlers", ())), SOURCE_COUNTER_REFLEXIVE_FIELD):
        return ()
    required = source_counter_reflexive_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.event != "step.begin" or program.active_zone != "battlefield"
            or thaw_value(program.event_condition) != source_counter_reflexive_condition()
            or program.cost_schema is not None or getattr(program, "handlers", ())):
        raise ValueError("A committed source counter needs its complete beginning-combat trigger")
    return required


def source_counter_placement_completed(results, *, source_id):
    if not results:
        return False
    if len(results) != 1 or not isinstance(results[0], CounterPlacementResult):
        raise ValueError("The reflexive trigger requires one actual source counter receipt")
    result = results[0]
    if (result.subject_kind != "permanent" or result.subject_id != source_id
            or result.counter_name != "+1/+1" or result.requested != 1
            or type(result.placed) is not int or result.placed < 0):
        raise ValueError("The reflexive trigger lost its original source counter event")
    return result.placed > 0


def commit_source_counter_reflexive(host, intent):
    parent = next((x for x in host.state.stack if x.ref == intent.parent_stack_ref), None)
    if (parent is None or parent.controller != intent.actor or intent.actor not in host.active_seats
            or parent.kind != "triggered_ability" or parent.context.get("currently_resolving") is not True):
        raise GameRuleError("A source counter reflexive result requires its original resolving trigger")
    program = host.semantics.get(parent.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise GameRuleError("Source counter reflexive creation requires its trusted CardProgram")
    source = host.state.cards.get(parent.source_object_id)
    if (source is None or source.zone != "battlefield" or source.phased_out
            or source.logical_object_id != parent.context.get("source_logical_object_id")
            or tuple(intent.object_refs) != (source.ref,) or intent.source_ref != source.ref):
        raise GameRuleError("The reflexive counter changed its original source")
    try:
        required = source_counter_reflexive_runtime_dependencies(program)
        if (not required or not set(required) <= set(program.capability_dependencies)
                or FrozenMap(program.effects[0][SOURCE_COUNTER_REFLEXIVE_FIELD]) != intent.child_program):
            raise ValueError("The reflexive child must match the admitted creating trigger")
        child, _ = source_counter_reflexive_child(intent.child_program)
        results = place_counters_on_refs(
            host, actor=intent.actor, object_refs=intent.object_refs, counter_name=intent.counter_name,
            amount=intent.amount, selections=intent.replacement_selections, reason=intent.reason,
            source_ref=source.ref, effect_generated=True,
        )
        if source_counter_placement_completed(results, source_id=source.object_id):
            from .reflexive_triggers import _place_reflexive_trigger

            _place_reflexive_trigger(host, parent, child)
    except ReplacementChoiceRequired:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return tuple(host.state.cards[result.object_id].ref for result in results)
