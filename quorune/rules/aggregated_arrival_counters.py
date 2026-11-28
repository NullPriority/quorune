"""Count distinct matching arrivals through the canonical trigger aggregation."""
from collections.abc import Mapping
import hashlib

from ..event_counter_reference import event_object_identity
from ..errors import GameRuleError
from ..util import stable_json

MATCHED_ARRIVAL_COUNT = "$event.matched_arrival_count"
MATCHED_ARRIVAL_CONTEXT = "matched_arrival_incarnations"
MATCHED_ARRIVAL_MECHANIC = "matching-token-arrival-counter-quantity"
MATCHED_ARRIVAL_CAPABILITY = "counter.producer.matching_token_arrival_count"


def matching_token_arrival_condition(value):
    return value == {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "token", "op": "truthy", "value": True},
    ]}


def matching_arrival_counter_effect():
    return {"op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1",
            "amount": MATCHED_ARRIVAL_COUNT, "source": "$source"}


def matching_arrival_counter_capabilities(effects, target_schema, mechanics):
    if (target_schema is None and tuple(effects) == (matching_arrival_counter_effect(),)
            and {MATCHED_ARRIVAL_MECHANIC, "cr-122-counters"}.issubset(mechanics)):
        return ("counter.producer.fixed_effect", MATCHED_ARRIVAL_CAPABILITY)
    return ()


def _contains_arrival_count(value):
    if isinstance(value, Mapping):
        return any(_contains_arrival_count(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_arrival_count(child) for child in value)
    return value == MATCHED_ARRIVAL_COUNT


def matching_arrival_counter_program_dependencies(program):
    if _contains_arrival_count((program.target_schema, program.cost_schema, program.handlers)):
        raise ValueError("Matching arrival count belongs only to the original triggered result")
    from .aggregated_arrival_damage import artifact_arrival_damage_program_dependencies, uses_artifact_arrival_damage

    if any(map(uses_artifact_arrival_damage, program.effects)):
        return artifact_arrival_damage_program_dependencies(program)
    uses_count = _contains_arrival_count(program.effects)
    if not uses_count and MATCHED_ARRIVAL_MECHANIC not in program.coverage:
        return ()
    deps = matching_arrival_counter_capabilities(program.effects, program.target_schema, program.coverage)
    if (not deps or program.event != "permanent.enter" or program.active_zone != "battlefield"
            or "one_or_more_event_batch" not in program.coverage
            or not matching_token_arrival_condition(program.event_condition)
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Matching arrival counters require one source-bound grouped token entry result")
    return deps


def matching_arrival_member_context(program, event, context):
    from .aggregated_arrival_damage import uses_artifact_arrival_damage

    artifacts = any(map(uses_artifact_arrival_damage, program.effects))
    if not artifacts and MATCHED_ARRIVAL_MECHANIC not in program.coverage:
        return {}
    qualifies = "artifact" in context.get("types", ()) if artifacts else context.get("token") is True
    if event != "permanent.enter" or not qualifies:
        raise ValueError("Matching arrival count requires the normalized qualifying entry")
    identity = event_object_identity(event, context)["logical_object_id"]
    return {MATCHED_ARRIVAL_CONTEXT: (identity,)}


def matched_arrival_identities(context):
    if not isinstance(context, Mapping):
        raise ValueError("Matching arrivals require sealed trigger context")
    values = context.get(MATCHED_ARRIVAL_CONTEXT)
    if (not isinstance(values, (tuple, list)) or not values
            or any(type(value) is not str or not value for value in values)
            or len(values) != len(set(values))):
        raise ValueError("Matching arrivals require unique public incarnations")
    return tuple(values)


def coalesce_trigger_aggregation(item, pending):
    """Extend the matching group while retaining each original trigger instance."""
    aggregation = item.context.get("one_or_more_aggregation_id")
    matching = (
        existing for existing in pending
        if existing.semantic_key == item.semantic_key
        and existing.source_object_id == item.source_object_id
        and existing.context.get("one_or_more_aggregation_id") == aggregation
    )
    if MATCHED_ARRIVAL_CONTEXT not in item.context:
        return next(matching, None) is not None

    found = False
    (new_member,) = matched_arrival_identities(item.context)
    for existing in matching:
        values = matched_arrival_identities(existing.context)
        if new_member not in values:
            existing.context[MATCHED_ARRIVAL_CONTEXT] = (*values, new_member)
        found = True
    return found


def capture_trigger_aggregation_context(stack_context, program, source, event, context):
    """Capture the source and occurrence identity for one aggregate trigger."""
    if "one_or_more_event_batch" in program.coverage:
        try:
            stack_context.update(matching_arrival_member_context(program, event, context))
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        aggregation_scope = (
            {"damaged_player": context.get("player")}
            if event == "damage.dealt"
            else {}
        )
        if event == 'creature.attacks':
            from .attack_batches import attack_batch_aggregation_scope, declared_attack_counter_context
            try:
                aggregation_scope = attack_batch_aggregation_scope(program, context)
                stack_context.update(declared_attack_counter_context(program, source, context))
            except (ValueError, TypeError) as exc:
                raise GameRuleError(str(exc)) from exc
        stack_context["one_or_more_aggregation_id"] = hashlib.sha256(
            stable_json(
                {
                    "event": event,
                    "event_id": context.get("event_id"),
                    **aggregation_scope,
                    "source_logical_object_id": source.logical_object_id,
                    "source_ability_id": program.key,
                }
            ).encode("utf-8")
        ).hexdigest()
