"""A committed exile batch with unconditional next-own-end-step damage."""
from collections.abc import Mapping

from ..damage_source import DamageSourceSnapshot
from ..impulse_access_model import ImpulseAccessDuration, POWER_COUNTED_IMPULSE_VERSION
from ..object_query import ObjectQuerySpec, query_objects
from ..replacement.immutable import FrozenMap, thaw_value
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition
from .stack_damage_sources import SOURCE_DAMAGE_CONTEXT, bound_stack_damage_source

CAPABILITY = "trigger.delayed.power_counted_impulse_damage"
DEPENDENCIES = (CAPABILITY, "zone.impulse_access.fixed", "zone.impulse_access.next_end_step",
                "zone.change.destination_replacement", "damage.amount.positive", "damage.result.player_life", "damage.source.stack_current_or_lki")
SOURCE = "$source"
TRIGGER_KINDS = {"triggered_ability", "triggered_ability_copy"}
RECEIPT = "power_counted_exile_damage"
CONDITION = {"player": "controller", "phase": "ending", "step": "end_step"}


def impulse_effect(threshold, damage):
    if type(threshold) is not int or threshold < 0 or type(damage) is not int or damage <= 0:
        raise ValueError("Impulse damage requires a fixed power qualification and positive rate")
    return {"op": "fixed_impulse_access", "schema_version": POWER_COUNTED_IMPULSE_VERSION,
            "player": "$controller", "count": {"power_at_least": threshold, "damage_per_card": damage},
            "duration": ImpulseAccessDuration.NEXT_END_STEP.value}


def validate_effect(effect):
    if (type(effect.get("schema_version")) is not int
            or FrozenMap(effect) != FrozenMap(impulse_effect(effect["count"]["power_at_least"],
                                                           effect["count"]["damage_per_card"]))):
        raise ValueError("Invalid power-counted impulse instruction")


def program_dependencies(program):
    effect, = program.effects
    validate_effect(effect)
    source = {"field": "card", "op": "eq", "value": "$source.ref"}
    condition = fixed_event_subscription_condition((
        FixedEventSubscription("permanent.enter", source),
        FixedEventSubscription("creature.attacks", source),
    ))
    if ((program.event, program.active_zone) != ("permanent.enter", "battlefield")
            or program.cost_schema is not None or program.handlers or program.target_schema is not None
            or not {"current_ability_fragment_required", "fixed_multi_event_subscription"} <= set(program.coverage)
            or FrozenMap(program.event_condition or {}) != FrozenMap(condition)):
        raise ValueError("Power-counted impulse requires the original untargeted battlefield trigger")
    return DEPENDENCIES


def _current_program(host, key):
    program = host.semantics.get(key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise ValueError("Power-counted impulse requires its trusted original trigger")
    program_dependencies(program)
    return program


def _validate_receipt(host, item, value):
    if (not isinstance(value, Mapping) or set(value) != {"semantic_key", "objects"}
            or not isinstance(value["objects"], (list, tuple))):
        raise ValueError("Delayed impulse damage requires a committed exile batch")
    program = _current_program(host, value["semantic_key"])
    ref = item.context.get("delayed_trigger_ref")
    original = next((trigger for trigger in host.state.delayed_triggers if trigger.ref == ref), None)
    instruction = {"op": "damage_each_opponent", "source": SOURCE, RECEIPT: value}
    if (item.kind not in TRIGGER_KINDS or item.context.get("event") != "step.begin" or original is None
            or original.active or not original.once or original.event_kind != "step.begin"
            or original.condition != CONDITION
            or (item.kind == "triggered_ability" and original.controller != item.controller)
            or original.source_object_id != item.source_object_id
            or original.source_logical_object_id != item.context.get("source_logical_object_id")
            or tuple(FrozenMap(effect) for effect in original.stack_template.get("context", {}).get("dynamic_effects", ()))
            != (FrozenMap(instruction),)):
        raise ValueError("Exile damage requires its original delayed occurrence")
    seen = set()
    count = 0
    for row in value["objects"]:
        if (not isinstance(row, Mapping) or set(row) != {"object_id", "logical_object_id", "ref"}
                or any(type(part) is not str or not part for part in row.values())
                or not row["logical_object_id"].startswith(row["object_id"] + "@") or row["object_id"] in seen):
            raise ValueError("Invalid original exile incarnation")
        seen.add(row["object_id"])
        card = host.state.cards.get(row["object_id"])
        if (card is not None and card.is_card_object and card.owner == original.controller and card.zone == "exile"
                and card.ref == row["ref"] and card.logical_object_id == row["logical_object_id"]):
            count += 1
    return count * program.effects[0]["count"]["damage_per_card"]


def bind_resolution(host, item, raw):
    if raw is None:
        return raw
    if raw.get("op") == "fixed_impulse_access" and raw.get("schema_version") == POWER_COUNTED_IMPULSE_VERSION:
        program = _current_program(host, item.semantic_key)
        if item.kind not in TRIGGER_KINDS or FrozenMap(raw) != FrozenMap(program.effects[0]):
            raise ValueError("Power-counted impulse lost its original triggered instruction")
        query = ObjectQuerySpec(zones=("battlefield",), controller=item.controller, types_all=("creature",),
            numeric_characteristic=TargetNumericCharacteristicSpec(TargetNumericCharacteristic.POWER,
                TargetNumericComparison.AT_LEAST, raw["count"]["power_at_least"]))
        count = len(query_objects(host.affected_permanent_object_rows(item.controller), query))
        return {**raw, "count": count}
    if RECEIPT not in raw:
        return raw
    if set(raw) != {"op", "source", RECEIPT} or raw["op"] != "damage_each_opponent" or raw["source"] != SOURCE:
        raise ValueError("Exile damage must preserve the exact linked damage instruction")
    amount = _validate_receipt(host, item, raw[RECEIPT])
    return {"op": "damage_each_opponent", "amount": amount, "source": SOURCE} if amount else None


def resolve_impulse(host, intent):
    from ..impulse_access import ImpulseAccessRequest, link_power_counted_play_permissions, resolve_fixed_impulse_access

    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.kind not in TRIGGER_KINDS or item.context.get("currently_resolving") is not True
            or item.controller != intent.actor):
        raise ValueError("Power-counted impulse lost its resolving trigger")
    _current_program(host, item.semantic_key)
    source = DamageSourceSnapshot.from_dict(bound_stack_damage_source(
        host, {"op": "damage_each_opponent", "source": SOURCE}, item)["source_snapshot"])
    result = resolve_fixed_impulse_access(host, ImpulseAccessRequest(
        actor=intent.actor, player=intent.player, count=intent.count, duration=intent.duration,
        reason=intent.reason, schema_version=intent.schema_version))
    cards = tuple(host._resolve_object(item.controller, ref, zones={"exile"}) for ref in result.exiled_refs)
    receipt = {"semantic_key": item.semantic_key, "objects": [
        {"object_id": card.object_id, "logical_object_id": card.logical_object_id, "ref": card.ref}
        for card in cards]}
    label = "Deal damage for cards remaining in exile"
    trigger = host.schedule_delayed_trigger(controller=item.controller, label=label, event_kind="step.begin",
        condition=CONDITION, source_object_id=source.object_id, source_logical_object_id=source.logical_object_id,
        referred_object_ids=tuple(card.object_id for card in cards),
        stack_template={"label": label, "context": {SOURCE_DAMAGE_CONTEXT: source.to_dict(),
            "dynamic_effects": [{"op": "damage_each_opponent", "source": SOURCE, RECEIPT: receipt}]}})
    link_power_counted_play_permissions(cards, trigger)
    return intent.player, result.exiled_refs
