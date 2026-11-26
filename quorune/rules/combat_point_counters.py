"""Typed combat point placement, removal, and actual-recipient consequences."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap, thaw_value


COMBAT_POINT_COUNTER_CAPABILITY = "trigger.event.combat_point_counter"
POINT_GAME_LOSS_FIELD = "point_game_loss"
POINT_COUNTER_OBSERVATION = "point_counter_observation"
INBOUND_POINT_VARIANT = "point_counter_received_combat_damage"
OUTBOUND_POINT_VARIANT = "point_counter_dealt_combat_damage"


def point_combat_condition(*, outbound):
    conditions = [
        {"field": "source_zone", "op": "eq", "value": "battlefield"},
        {"field": "source_types", "op": "contains_any", "value": ["creature"]},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
        {"field": "target", "op": "ne" if outbound else "eq", "value": "$source.controller"},
    ]
    if outbound:
        conditions.append({"field": "source_controller", "op": "eq", "value": "$source.controller"})
    return {"all": conditions}


def point_combat_effects(*, outbound):
    from ..resolution_conditions import RESOLUTION_CONDITION_OPERATION

    change = {"op": "place_counters" if outbound else "remove_counters",
              "card": "$source.zone_object", "counter": "point", "amount": 1, "source": "$source"}
    if not outbound:
        return (change,)
    return (change, {"op": RESOLUTION_CONDITION_OPERATION, "player": "$controller",
                      POINT_GAME_LOSS_FIELD: {"schema_version": 1, "counter": "point", "minimum": 10}})


def uses_point_game_loss(effect):
    return isinstance(effect, Mapping) and POINT_GAME_LOSS_FIELD in effect


def _same_point_effects(effects, *, outbound):
    if len(effects) != (2 if outbound else 1) or type(effects[0].get("amount")) is not int:
        return False
    if outbound:
        spec = effects[1].get(POINT_GAME_LOSS_FIELD)
        if (not isinstance(spec, Mapping) or type(spec.get("schema_version")) is not int
                or type(spec.get("minimum")) is not int):
            return False
    return thaw_value(effects) == thaw_value(point_combat_effects(outbound=outbound))


def point_combat_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if target_schema is not None or "cr-122-counters" not in mechanics:
        return ()
    required = (COMBAT_POINT_COUNTER_CAPABILITY, "trigger.effect.fixed_event",
                "trigger.event.normalized_damage", "trigger.placement.apnap")
    if _same_point_effects(effects, outbound=False):
        return (*required, "counter.removal.fixed_effect", "counter.removal.fixed_source_effect")
    if _same_point_effects(effects, outbound=True):
        return (*required, "counter.producer.fixed_effect", "counter.producer.fixed_event_trigger",
                "resolution.effect.public_condition",
                "counter.removal.all_effect", "counter.removal.all_named_source_effect", "game.loss.effect")
    return ()


def point_combat_runtime_dependencies(program):
    inbound = (program.event == "damage.dealt" and _same_point_effects(program.effects, outbound=False)
               and FrozenMap(program.event_condition or {}) == FrozenMap(point_combat_condition(outbound=False)))
    if (not inbound and COMBAT_POINT_COUNTER_CAPABILITY not in program.capability_dependencies
            and not any(uses_point_game_loss(effect) for effect in program.effects)):
        return ()
    owned = point_combat_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not owned or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or program.handlers or program.cost_schema is not None or "one_or_more_event_batch" in program.coverage
            or FrozenMap(program.event_condition or {}) != FrozenMap(
                point_combat_condition(outbound=len(program.effects) == 2),
            )):
        raise ValueError("Combat point results require their exact distinct damage and recipient condition")
    return owned


def initial_point_counter_observation(source, current):
    observed = {"counter": "point", "reference": source.ref, "logical_object_id": source.logical_object_id,
                "departure_count": None}
    if not _same_source(current, observed):
        observed["departure_count"] = source.counters.get("point", 0)
    return {POINT_COUNTER_OBSERVATION: observed}


def _same_source(card, observed):
    return (card is not None and card.zone == "battlefield" and card.ref == observed["reference"]
            and card.logical_object_id == observed["logical_object_id"])


def point_counter_observation(context):
    observed = context.get(POINT_COUNTER_OBSERVATION)
    if (not isinstance(observed, Mapping)
            or set(observed) != {"counter", "reference", "logical_object_id", "departure_count"}
            or observed["counter"] != "point"
            or observed["departure_count"] is not None and
                (type(observed["departure_count"]) is not int or observed["departure_count"] < 0)
            or any(type(observed[key]) is not str or not observed[key] for key in ("reference", "logical_object_id"))):
        raise ValueError("Combat point threshold lost its original source observation")
    return observed, observed


def bind_point_game_loss(host, effect, item):
    from ..semantic_runtime.context import SemanticNodeError

    try:
        if FrozenMap(effect) != FrozenMap(point_combat_effects(outbound=True)[1]):
            raise ValueError("Combat point threshold is malformed")
        context = item.context
        observed, _ = point_counter_observation(context)
        target = context.get("target")
        if (item.kind != "triggered_ability" or context.get("event") != "damage.dealt"
                or context.get("target_kind") != "player" or context.get("combat") is not True
                or target not in host.state.players or target == item.controller
                or observed["logical_object_id"] != context.get("source_logical_object_id")):
            raise ValueError("Combat point threshold lost its own actual damaged opponent")
        current = host.state.cards.get(item.source_object_id)
        matching = _same_source(current, observed)
        count = current.counters.get("point", 0) if matching else observed["departure_count"]
        if type(count) is not int or count < 0:
            raise ValueError("Combat point threshold lacks a valid current or departure count")
        return {**effect, "player": item.controller, "target": target,
                "remove_from": current.ref if matching and not current.phased_out else None, "count": count}
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


def point_game_loss_bound_results(effect, *, actor, stack_controller):
    base = {key: effect[key] for key in ("op", "player", POINT_GAME_LOSS_FIELD)}
    if (set(effect) != {*base, "target", "remove_from", "count"}
            or FrozenMap(base) != FrozenMap({**point_combat_effects(outbound=True)[1], "player": actor})
            or type(effect[POINT_GAME_LOSS_FIELD]["minimum"]) is not int
            or actor != stack_controller or type(effect["target"]) is not str or effect["target"] == actor
            or type(effect["count"]) is not int or effect["count"] < 0):
        raise ValueError("Combat point consequence needs its bound source and damaged opponent")
    if effect["count"] < 10:
        return ()
    source = effect["remove_from"]
    if source is not None and (type(source) is not str or not source):
        raise ValueError("Combat point removal needs its original permanent")
    removal = ({"op": "remove_all_counters", "schema_version": 2, "card": source,
                "counter": "point", "source": source},) if source is not None else ()
    return (*removal, {"op": "lose_game", "player": effect["target"]})
