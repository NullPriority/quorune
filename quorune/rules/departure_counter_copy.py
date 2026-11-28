"""Copy a departed creature's sealed counters onto the original observing permanent."""

from ..counter_snapshot import permanent_counter_snapshot
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects


DEPARTURE_COUNTER_COPY_CAPABILITY = "counter.producer.departure_lki_copy"
DEPARTURE_COUNTER_COPY_MECHANIC = "departure-lki-counter-copy"
DEPARTURE_COUNTERS = "departure_counter_copy_snapshot"
DEPARTURE_HAS_COUNTERS = "departure_counter_copy_has_counters"
DEPARTURE_COUNTER_COPY_DEPENDENCIES = (
    DEPARTURE_COUNTER_COPY_CAPABILITY,
    "counter.producer.fixed_event_trigger",
    "counter.producer.fixed_multikind_effect",
    "counter.placement.quantity_replacement",
    "trigger.event.normalized_zone_change",
    "trigger.placement.apnap",
)


def departure_counter_copy_condition():
    return {"all": [
        {"field": "types", "op": "contains_any", "value": ["creature"]},
        {"field": "previous_controller", "op": "eq", "value": "$source.controller"},
        {"field": DEPARTURE_HAS_COUNTERS, "op": "eq", "value": True},
    ]}


def departure_counter_copy_effect():
    return {"op": "place_counter_batch", "card": "$source", DEPARTURE_COUNTERS: "$context." + DEPARTURE_COUNTERS}


def uses_departure_counter_copy(effect):
    return effect.get("op") == "place_counter_batch" and DEPARTURE_COUNTERS in effect


def departure_counter_copy_node_capabilities(*, effects, target_schema, mechanics, cost_schema):
    if (DEPARTURE_COUNTER_COPY_MECHANIC not in mechanics or target_schema is not None
            or cost_schema is not None or len(effects) != 1
            or thaw_value(effects[0]) != departure_counter_copy_effect()):
        return ()
    return DEPARTURE_COUNTER_COPY_DEPENDENCIES


def departure_counter_copy_program_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if uses_departure_counter_copy(effect))
    if not marked:
        if DEPARTURE_COUNTER_COPY_CAPABILITY in program.capability_dependencies:
            raise ValueError("Departure counter copy lost the original bound effect")
        return ()
    if (effects != marked or program.event != "permanent.leave" or program.active_zone != "battlefield"
            or program.handlers or thaw_value(program.event_condition) != departure_counter_copy_condition()):
        raise ValueError("A departure counter copy requires the original controlled creature occurrence")
    required = departure_counter_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanics=program.coverage, cost_schema=program.cost_schema,
    )
    if not required:
        raise ValueError("A departure counter copy requires one original LKI result")
    return required


def departure_counter_copy_context(host, event, context, occurrence):
    from .event_subscriptions import event_has_capability_subscriber

    if event != "permanent.leave" or not event_has_capability_subscriber(
        host, event, DEPARTURE_COUNTER_COPY_CAPABILITY,
    ):
        return context
    snapshot = permanent_counter_snapshot(occurrence.previous_counters or {})
    return {**context, DEPARTURE_COUNTERS: dict(snapshot), DEPARTURE_HAS_COUNTERS: bool(snapshot)}


def departure_counter_copy_intent(effect, context):
    from ..semantic_runtime.context import SemanticNodeError
    from ..semantic_runtime.direct_target_fields import validate_direct_target_effect
    from ..semantic_runtime.intents import CounterPlacementAmount, PlaceCounterBatchIntent

    fields = validate_direct_target_effect(
        effect, context, operation="place_counter_batch", reference_field="card",
        family_label="Copy departure counters", allow_replacement_selections=True,
        additional_allowed_fields=(DEPARTURE_COUNTERS,),
    )
    source = context.source
    if source is None or source.logical_object_id is None or source.card_ref != fields.object_ref:
        return None
    try:
        snapshot = permanent_counter_snapshot(effect[DEPARTURE_COUNTERS])
        return PlaceCounterBatchIntent(
            actor=context.actor, object_ref=fields.object_ref, reason=fields.reason,
            source_ref=source.stack_ref, replacement_selections=fields.replacement_selections,
            recipient_identity=source.logical_object_id, source_departure=True,
            placements=tuple(CounterPlacementAmount(name, amount) for name, amount in snapshot.items()),
        )
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc
