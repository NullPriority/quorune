"""One tapped mana source adds its own public point counter through replacement."""

from collections.abc import Mapping

from ..counter_placement import (
    CounterPlacementError, CounterPlacementRequest, commit_prepared_counter_placements, prepare_counter_placements,
)
from ..errors import GameRuleError


def _event_source(event_id, payment_id):
    if not isinstance(payment_id, str) or not payment_id or not isinstance(event_id, str):
        return None
    prefix = f"counter.place.mana:{payment_id}:"
    if not event_id.startswith(prefix):
        return None
    source, separator, ordinal = event_id[len(prefix):].rpartition(":")
    if (source and separator and ordinal.isascii() and ordinal.isdecimal()
            and (ordinal == "0" or not ordinal.startswith("0"))):
        return source
    return None


def mana_point_event_belongs_to_payment(event_id, payment_id):
    return _event_source(event_id, payment_id) is not None


def mana_point_replacement_event(batch, response, seat):
    if len(batch.events) != 1:
        return None
    event = batch.events[0]
    source = _event_source(event.event_id, response.get("_mana_payment_id"))
    payload = event.payload
    if (source is not None and event.kind == "counter.place"
            and event.affected_object is not None and event.affected_object.controller == seat
            and payload.get("target_controller") == seat and payload.get("placing_player") == seat
            and payload.get("target_zone") == "battlefield" and payload.get("target_kind") == "permanent"
            and payload.get("source") == source and payload.get("counter_name") == "point"
            and payload.get("effect_generated") is True and type(payload.get("requested_amount")) is int
            and payload["requested_amount"] == 1 and type(payload.get("amount")) is int and payload["amount"] >= 0):
        return event
    return None


def apply_mana_point_counter(host, seat, index, *, source, payment_id, selections_by_event):
    live = host.state.cards.get(source.object_id) if source is not None else None
    if (live is None or live.logical_object_id != source.logical_object_id
            or live.zone != "battlefield" or live.phased_out or live.controller != seat or not live.tapped):
        raise GameRuleError("A point-counter mana result requires its original tapped battlefield source")
    event_id = f"counter.place.mana:{payment_id}:{live.ref}:{index}" if payment_id else None
    picks = selections_by_event.get(event_id, ()) if event_id and isinstance(selections_by_event, Mapping) else ()
    if not isinstance(picks, (list, tuple)):
        raise GameRuleError("Point-counter mana replacement selections are malformed")
    try:
        prepared = prepare_counter_placements(
            host, (CounterPlacementRequest("permanent", live.object_id, "point", 1, seat, live.ref),),
            selections=tuple(picks), event_ids=(event_id,) if event_id else None,
        )
        commit_prepared_counter_placements(host, prepared, reason="mana ability point counter")
    except CounterPlacementError as exc:
        raise GameRuleError(str(exc)) from exc


def is_unmodified_point_mana(ability, restriction, output):
    return (output.casefold() == "add {c}" and ability.cost_text.strip() == "{T}"
            and ability.tap_source and not any((
        ability.sacrifice_source, ability.life_payment, ability.choices, ability.activation_limit,
        ability.activation_conditions, restriction, ability.source_counter_removal_cost,
        ability.mana_cost_options, sum(ability.mana.values()),
    )))


def mana_point_runtime_dependencies(program):
    from ..fixed_mana_abilities import FIXED_MANA_RESULT_HANDLER_ID, FixedActivatedManaAbilitySpec
    from ..fixed_mana_results import FIXED_MANA_POINT_COUNTER_CAPABILITY
    from ..compiler.activated_costs import activated_ability_cost
    from ..replacement.immutable import FrozenMap

    for handler in program.handlers:
        if handler.get("handler_id") != FIXED_MANA_RESULT_HANDLER_ID:
            continue
        parsed = FixedActivatedManaAbilitySpec.from_dict(handler["ability"])
        if parsed.result is not None and parsed.result.schema_version == 4:
            catalogs = tuple(h for h in program.handlers if h is not handler)
            expected_cost = FrozenMap(activated_ability_cost(parsed.to_activated_ability()))
            if (program.event != "activate" or program.active_zone != "battlefield" or program.effects
                    or program.target_schema is not None or FrozenMap(program.cost_schema or {}) != expected_cost
                    or len(catalogs) > 1 or any(h.get("handler_id") != "activation.catalog.pinned.v1" for h in catalogs)):
                raise ValueError("A mana point counter requires its exact battlefield mana activation")
            return (FIXED_MANA_POINT_COUNTER_CAPABILITY,)
    return ()
