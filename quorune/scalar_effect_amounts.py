from __future__ import annotations

"""Read-only scalar evaluation and existing stack-context departure pinning."""

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .object_query import exact_numeric_characteristic
from .counter_names import normalized_counter_name
from .query_effect_amount_model import PublicQueryAmountError
from .scalar_effect_amount_model import (
    PAID_REVEALED_CREATURE_CONTEXT, PAID_SACRIFICE_MANA_VALUE_CONTEXT, PAID_SACRIFICE_STAT_CONTEXT,
    PaidCreatureStatSnapshot, PaidRevealedCreatureSnapshot, PaidSacrificeManaValueSnapshot,
    SCALAR_REFERENCE_CONTEXT, SOURCE_COUNTER_KIND_COUNT_VERSION, ScalarAmountOrigin, ScalarEffectAmountSpec,
    scalar_amount_specs,
)
from .turn_history import current_turn_history_events
from .paid_sacrifice_conditions import (
    PAID_SACRIFICE_CHARACTERISTICS_CONTEXT, PaidSacrificeCharacteristics, paid_sacrifice_condition_from_reference,
)
from .drawing.restrictions import drawn_this_turn


class ScalarAmountHost(Protocol):
    state: Any
    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...
    def _type_parts(self, text: str) -> tuple[set[str], set[str], set[str]]: ...


def _snapshot(host, card, *, characteristics=None, include_counters=False, include_mutations=False,
              requested_characteristic=None):
    data = dict(characteristics) if characteristics is not None else host._effective_card_data(card)
    values = {'power': None, 'toughness': None}
    if requested_characteristic != 'mana_value':
        types = host._type_parts(str(data.get("type_line") or ""))[0]
        has_stats = "creature" in types or card.zone != "battlefield"
        values = {field: (0 if "creature" not in types and data.get(field) is None else exact_numeric_characteristic(card, data, field)) if has_stats else 0
                  for field in ("power", "toughness")}
    values["mana_value"] = data.get("mana_value", data.get("cmc"))
    return {"object_id": card.object_id, "logical_object_id": card.logical_object_id,
            "ref": card.ref, "zone": card.zone, **values,
            **({"counters": dict(card.counters)} if include_counters else {}),
            **({"mutation_count": _mutation_count(card)} if include_mutations else {})}


def _mutation_count(card):
    composition = card.permanent_composition
    if composition is None:
        return 0
    composition.validate_carrier(card)
    return composition.topology.mutation_count


def _current_card(host, ref):
    return next((card for card in host.state.cards.values() if card.ref == ref), None)


def scalar_source_context(host, source, effects, *, characteristics=None):
    """Seed source identity/LKI before activation costs or departure discovery."""
    specs = scalar_amount_specs(effects)
    if not any(spec.origin is ScalarAmountOrigin.SOURCE for spec in specs):
        return {}
    return {SCALAR_REFERENCE_CONTEXT: {"source": _snapshot(host, source, characteristics=characteristics,
        include_counters=any(spec.reads_source_counters for spec in specs),
        include_mutations=any(spec.reads_source_mutation_count for spec in specs))}}


def _entry_mana_value_reference(spec, item):
    return (
        spec.origin is ScalarAmountOrigin.EVENT_OBJECT and spec.characteristic == "mana_value"
        and item.context.get("event") == "permanent.enter"
    )


def _reference(host, item, spec, *, use_phased_source_lki=False):
    snapshots = item.context.setdefault(SCALAR_REFERENCE_CONTEXT, {})
    if not isinstance(snapshots, dict):
        raise PublicQueryAmountError("Scalar reference continuation is malformed")
    key = spec.origin.value
    if spec.origin is ScalarAmountOrigin.SOURCE:
        key = "source"
        initial = snapshots.get(key)
        if initial is None:
            card = host.state.cards.get(item.source_object_id or item.card_object_id or "")
            if card is None or card.logical_object_id != item.context.get("source_logical_object_id"):
                raise PublicQueryAmountError("Scalar source identity is unavailable")
            initial = _snapshot(host, card, include_counters=spec.reads_source_counters,
                                include_mutations=spec.reads_source_mutation_count)
    elif spec.origin in {
        ScalarAmountOrigin.TARGET, ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE, ScalarAmountOrigin.TARGET_CREATURE_POWER,
    }:
        if len(item.targets) != 1 or item.targets[0] is None:
            raise PublicQueryAmountError("Scalar characteristic requires its single target")
        ref = item.targets[0]
        initial = snapshots.get(key)
        if initial is None:
            target = item.context.get("target_snapshots", {}).get(ref)
            card = _current_card(host, ref)
            if not isinstance(target, Mapping) or card is None or card.zone_change_counter != target.get("zone_change_counter"):
                raise PublicQueryAmountError("Scalar target identity is unavailable")
            if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE and card.zone != 'graveyard':
                raise PublicQueryAmountError('Scalar graveyard target left its expected public zone')
            initial = _snapshot(host, card, requested_characteristic='mana_value'
                if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE else None)
    else:
        initial = snapshots.get(key)
        if initial is None:
            context = item.context.get("event_context", item.context)
            if not isinstance(context, Mapping):
                raise PublicQueryAmountError("Scalar event reference is unavailable")
            ref = context.get("card")
            identity = context.get("card_object_identity")
            card = _current_card(host, ref)
            if type(identity) is not str or not identity or card is None:
                raise PublicQueryAmountError("Scalar event object identity is unavailable")
            initial = {"ref": ref, "object_id": card.object_id,
                "logical_object_id": identity, "zone": "battlefield",
                "power": context.get("power"), "toughness": context.get("toughness"),
                "mana_value": context.get("mana_value")}
    fields={"ref", "object_id", "logical_object_id", "zone", "power", "toughness", "mana_value"}
    if spec.reads_source_mutation_count:
        fields.add("mutation_count")
    if not isinstance(initial, Mapping) or set(initial) not in (fields, fields | {"counters"}):
        raise PublicQueryAmountError("Scalar characteristic snapshot is malformed")
    if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE and (
            len(item.targets) != 1 or initial['ref'] != item.targets[0] or initial['zone'] != 'graveyard'):
        raise PublicQueryAmountError('Scalar graveyard snapshot lost its target or expected public zone')
    if spec.reads_source_mutation_count and (initial["zone"] != "battlefield"
            or initial["object_id"] != item.source_object_id
            or initial["logical_object_id"] != item.context.get("source_logical_object_id")):
        raise PublicQueryAmountError("The mutation count lost its original battlefield source identity")
    snapshots[key] = dict(initial)
    card = host.state.cards.get(initial["object_id"])
    if card is not None and card.logical_object_id == initial["logical_object_id"] and card.zone == initial["zone"]:
        if card.phased_out:
            if (use_phased_source_lki and spec.origin is ScalarAmountOrigin.SOURCE) or _entry_mana_value_reference(spec, item):
                return initial
            raise PublicQueryAmountError("Scalar characteristic object is phased out")
        return _snapshot(host, card, include_counters="counters" in initial or spec.reads_source_counters,
            include_mutations="mutation_count" in initial or spec.reads_source_mutation_count,
            requested_characteristic=spec.characteristic if _entry_mana_value_reference(spec, item)
            or spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE else None)
    return initial


def _reference_identity(host, item, spec):
    """Locate a pending reference without evaluating or caching its value."""
    key = "source" if spec.origin is ScalarAmountOrigin.SOURCE else spec.origin.value
    snapshots = item.context.get(SCALAR_REFERENCE_CONTEXT)
    initial = snapshots.get(key) if isinstance(snapshots, Mapping) else None
    if isinstance(initial, Mapping):
        identity = initial.get("object_id"), initial.get("logical_object_id"), initial.get("zone")
        return identity if all(type(value) is str and value for value in identity) else None

    if spec.origin is ScalarAmountOrigin.SOURCE:
        object_id = item.source_object_id or item.card_object_id or ""
        card = host.state.cards.get(object_id)
        expected_identity = item.context.get("source_logical_object_id")
        if card is None or card.logical_object_id != expected_identity:
            return None
        return card.object_id, expected_identity, card.zone

    if spec.origin in {
        ScalarAmountOrigin.TARGET, ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE, ScalarAmountOrigin.TARGET_CREATURE_POWER,
    }:
        if len(item.targets) != 1 or item.targets[0] is None:
            return None
        ref = item.targets[0]
        target_snapshots = item.context.get("target_snapshots")
        target = target_snapshots.get(ref) if isinstance(target_snapshots, Mapping) else None
        card = _current_card(host, ref)
        if not isinstance(target, Mapping) or card is None or card.zone_change_counter != target.get("zone_change_counter"):
            return None
        zone = 'graveyard' if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE else 'battlefield'
        return card.object_id, card.logical_object_id, zone

    context = item.context.get("event_context", item.context)
    if not isinstance(context, Mapping):
        return None
    card = _current_card(host, context.get("card"))
    if card is None:
        return None
    return card.object_id, context.get("card_object_identity"), "battlefield"


def _shared_counter_result_amount(effects: Sequence[Any]):
    from .fixed_token_production import FIXED_DISTRIBUTED_TOKEN_OPERATION

    if len(effects) != 3 or tuple(effect.get("op") for effect in effects) != (
        "place_counters", FIXED_DISTRIBUTED_TOKEN_OPERATION, "damage_fixed_set",
    ):
        return None
    placement, production, damage = effects
    token = production.get("token")
    if not isinstance(token, Mapping):
        return None
    amount = token.get("quantity")
    if (not isinstance(amount, Mapping) or amount != damage.get("amount")
            or placement.get("card") != "$source.zone_object"
            or damage.get("source") != "$source"):
        return None
    spec = ScalarEffectAmountSpec.from_dict(amount)
    return amount if (spec.reads_source_counters and spec.binding_id is not None
                      and spec.counter_name == placement.get("counter")) else None


def pin_scalar_characteristic_departures(host, cards: Sequence[Any], *, error_type=PublicQueryAmountError, phase_out=False):
    """Use the original object immediately before a supported loss of availability."""
    from .counter_placement_sets import uses_source_power_combat_counter_set
    from .library_bottom_result_model import library_bottom_uses_source_power
    from .rules.opponent_event_mana_payment import uses_opponent_source_power_payment
    from .rules.counter_scaled_annihilator import uses_counter_scaled_annihilator
    from .rules.source_counter_kind_draw import uses_source_counter_kind_draw
    from .rules.source_counter_mill_choice import OFFER_FIELD as SOURCE_COUNTER_MILL_OFFER
    from .rules.source_power_firebending import uses_source_power_firebending

    try:
        departing = {card.object_id: card for card in cards if card.zone in {"battlefield", "graveyard"}}
        if not departing:
            return
        updates = []
        for item in host.state.stack:
            program = host.semantics.get(item.semantic_key) if item.semantic_key else None
            if program is None:
                continue
            specs = scalar_amount_specs(program.effects)
            phased_source_lki = (any(spec.reads_source_mutation_count for spec in specs)
                or library_bottom_uses_source_power(program.effects)
                or _shared_counter_result_amount(program.effects) is not None
                or any(SOURCE_COUNTER_MILL_OFFER in effect for effect in program.effects)
                or any(uses_source_counter_kind_draw(effect) for effect in program.effects)
                or any(uses_source_power_combat_counter_set(effect) or uses_opponent_source_power_payment(effect)
                       or uses_counter_scaled_annihilator(effect) or uses_source_power_firebending(effect)
                       for effect in program.effects))
            selected = [spec for spec in specs if spec.origin in {
                ScalarAmountOrigin.SOURCE, ScalarAmountOrigin.TARGET, ScalarAmountOrigin.EVENT_OBJECT,
                ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE, ScalarAmountOrigin.TARGET_CREATURE_POWER}]
            for spec in selected:
                if phase_out and not (
                    phased_source_lki and spec.origin is ScalarAmountOrigin.SOURCE or _entry_mana_value_reference(spec, item)
                ):
                    continue
                identity = _reference_identity(host, item, spec)
                if identity is None:
                    continue
                object_id, logical_object_id, zone = identity
                card = departing.get(object_id)
                expected_zone = 'graveyard' if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE else 'battlefield'
                if card is None or zone != expected_zone or card.zone != expected_zone or card.logical_object_id != logical_object_id:
                    continue
                snapshot = _reference(host, item, spec, use_phased_source_lki=phased_source_lki)
                key = "source" if spec.origin is ScalarAmountOrigin.SOURCE else spec.origin.value
                updates.append((item, key, snapshot))
        for item, key, value in updates:
            item.context.setdefault(SCALAR_REFERENCE_CONTEXT, {})[key] = value
    except (KeyError, TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def library_bottom_source_power(host, item):
    """Compare the original public source's signed power after the committed move."""
    from .library_bottom_result_model import LIBRARY_BOTTOM_SOURCE_POWER

    power = _reference(host, item, LIBRARY_BOTTOM_SOURCE_POWER, use_phased_source_lki=True)["power"]
    return power if type(power) is int else 0


def _history_amount(host, item, fact):
    history = host.state.turn_history
    if history is None or history.schema_version != 1 or history.turn_sequence != host.state.turn_sequence:
        raise PublicQueryAmountError("Scalar turn history is unavailable")
    if fact == "cards_drawn":
        return drawn_this_turn(host, item.controller)
    kind = {"life_gained":"player_gained_life", "life_lost":"player_lost_life",
        "spells_cast":"spell_cast",
        "permanents_sacrificed":"permanent_sacrificed", "creatures_died":"creature_died",
        "cards_discarded":"card_discarded",
        "creatures_entered":"permanent_entered"}[fact]
    events = current_turn_history_events(history, turn_sequence=host.state.turn_sequence, kind=kind)
    if fact in {"life_gained", "life_lost"}:
        return sum(event.amount for event in events if event.target == item.controller)
    if fact == "creatures_died":
        return len(events)
    if fact == "creatures_entered":
        return sum("creature" in event.types for event in events if event.actor == item.controller)
    return sum(event.actor == item.controller for event in events)


def _hand_count_target(item,spec):
    if spec.target_group is not None:
        groups=item.context.get('target_groups_current',item.context.get('target_groups',{}))
        targets=groups.get(spec.target_group) if isinstance(groups,Mapping) else None
        if not isinstance(targets,(tuple,list)) or len(targets)!=1 or type(targets[0]) is not str or not targets[0]:
            raise PublicQueryAmountError('Scoped hand count requires one current selected player')
        return targets[0]
    if len(item.targets)!=1 or type(item.targets[0]) is not str or not item.targets[0]:
        raise PublicQueryAmountError('Target hand count requires one selected player')
    return item.targets[0]


def _current_target_hand_count(host,seat):
    player=host.state.players.get(seat)
    if player is None or not player.in_game or not isinstance(player.zones.get('hand'),list):
        raise PublicQueryAmountError('Target hand count player is unavailable')
    return len(player.zones['hand'])


def _validate_paid_incarnation(item, logical_object_id, label):
    if item.kind not in {'spell', 'spell_copy'}:
        raise PublicQueryAmountError(f'{label} require their resolving spell or copy')
    if item.kind == 'spell':
        objects = item.context.get('fixed_cast_lifecycle_cost_objects')
        if not isinstance(objects, (list, tuple)) or sum(
            isinstance(row, Mapping) and row.get('logical_object_id') == logical_object_id
            for row in objects
        ) != 1:
            raise PublicQueryAmountError(f'{label} changed their cost incarnation')


def _paid_creature_stat_amount(item, characteristic):
    snapshot = PaidCreatureStatSnapshot.from_dict(item.context.get(PAID_SACRIFICE_STAT_CONTEXT))
    _validate_paid_incarnation(item, snapshot.logical_object_id, 'Paid creature stats')
    return getattr(snapshot, characteristic)


def resolve_paid_sacrifice_condition(value: Mapping, item) -> bool:
    condition = paid_sacrifice_condition_from_reference(value)
    from .paid_sacrifice_conditions import PAID_SUSPECTED_CONDITION_KIND, is_activation_paid_creature_reference

    if is_activation_paid_creature_reference(value):
        from .activation_paid_characteristic_model import (
            resolve_activation_paid_creature_characteristics, resolve_activation_paid_creature_suspected,
        )

        if condition.kind == PAID_SUSPECTED_CONDITION_KIND:
            return resolve_activation_paid_creature_suspected(item)
        return condition.matches(resolve_activation_paid_creature_characteristics(item))
    snapshot = PaidSacrificeCharacteristics.from_dict(item.context.get(PAID_SACRIFICE_CHARACTERISTICS_CONTEXT))
    _validate_paid_incarnation(item, snapshot.logical_object_id, "Paid sacrifice characteristics")
    return condition.matches(snapshot)


def _paid_sacrifice_mana_value(item):
    snapshot = PaidSacrificeManaValueSnapshot.from_dict(item.context.get(PAID_SACRIFICE_MANA_VALUE_CONTEXT))
    _validate_paid_incarnation(item, snapshot.logical_object_id, 'Paid sacrifice mana-value facts')
    return snapshot.mana_value


def _paid_revealed_creature_amount(item, characteristic):
    snapshot = PaidRevealedCreatureSnapshot.from_dict(item.context.get(PAID_REVEALED_CREATURE_CONTEXT))
    same_instance = item.ref == snapshot.stack_ref
    if (item.kind, same_instance) not in {("spell", True), ("spell_copy", False)}:
        raise PublicQueryAmountError("Paid reveal characteristics require the committed spell or its copy")
    return getattr(snapshot, characteristic)


def _reflexive_paid_creature_power(item, spec: ScalarEffectAmountSpec) -> int:
    from .reflexive_trigger_model import REFLEXIVE_PAID_CREATURE_CONTEXT, ReflexivePaidCreatureSnapshot

    if (item.kind != 'triggered_ability' or item.context.get('event') != 'trigger.reflexive'
            or not item.context.get('reflexive_parent')):
        raise PublicQueryAmountError('Reflexive paid power requires its triggered consequence')
    snapshot = ReflexivePaidCreatureSnapshot.from_dict(item.context.get(REFLEXIVE_PAID_CREATURE_CONTEXT))
    return snapshot.stats.power * (2 if spec.double_for_subtype in snapshot.subtypes else 1)


def resolve_scalar_effect_amount(host: ScalarAmountHost, value: Mapping[str, Any], item, *, use_phased_source_lki=False) -> int:
    spec = ScalarEffectAmountSpec.from_dict(value)
    from .cast_event_amounts import committed_cast_mana_value_facts
    cast_facts=committed_cast_mana_value_facts(item) if spec.origin is ScalarAmountOrigin.CAST_SPELL_MANA_VALUE else None
    cache = item.context.setdefault("declared_scalar_amounts", {}) if spec.binding_id else None
    identity = {"controller": item.controller, **spec.producer_identity}
    if cast_facts is not None:identity['cast_spell']=cast_facts
    hand_target=_hand_count_target(item,spec) if spec.origin is ScalarAmountOrigin.TARGET_HAND_COUNT else None
    if hand_target is not None:identity['target_player']=hand_target
    if cache is not None:
        if not isinstance(cache, dict):
            raise PublicQueryAmountError("Scalar declaration cache is malformed")
        previous = cache.get(spec.binding_id)
        if previous is not None:
            if not isinstance(previous, Mapping) or set(previous) != {"identity", "value"} or previous["identity"] != identity or type(previous["value"]) is not int or previous["value"] < 0:
                raise PublicQueryAmountError("Scalar declaration changed identity or value")
            return spec.coefficient * previous["value"]
    if spec.origin is ScalarAmountOrigin.PAID_ACTIVATION_CREATURE:
        from .activation_paid_characteristic_model import resolve_activation_paid_creature_stat
        amount = resolve_activation_paid_creature_stat(item, spec.characteristic)
    elif spec.origin is ScalarAmountOrigin.PAID_SACRIFICE_CREATURE:
        amount = _paid_creature_stat_amount(item, spec.characteristic)
    elif spec.origin is ScalarAmountOrigin.PAID_SACRIFICE_MANA_VALUE:
        amount = _paid_sacrifice_mana_value(item)
    elif spec.origin is ScalarAmountOrigin.PAID_REVEALED_CREATURE:
        amount = _paid_revealed_creature_amount(item, spec.characteristic)
    elif spec.origin is ScalarAmountOrigin.REFLEXIVE_PAID_CREATURE:
        amount = _reflexive_paid_creature_power(item, spec)
    elif spec.origin is ScalarAmountOrigin.CAST_SPELL_MANA_VALUE:
        amount=cast_facts['mana_value']
    elif spec.origin is ScalarAmountOrigin.CAST_MANA_COLOR_COUNT:
        from .mana_spent_amounts import resolve_spent_mana_color_amount
        amount = resolve_spent_mana_color_amount(item)
    elif spec.origin is ScalarAmountOrigin.MULTIKICKER_COUNT:
        from .multikicker_result_amounts import resolve_multikicker_amount
        amount = resolve_multikicker_amount(spec, item)
    elif spec.origin is ScalarAmountOrigin.TARGET_HAND_COUNT:
        amount=_current_target_hand_count(host,hand_target)
    elif spec.origin is ScalarAmountOrigin.UPKEEP_HAND_DIFFERENCE:
        from .rules.upkeep_hand_differences import resolve_upkeep_hand_difference
        amount=resolve_upkeep_hand_difference(host,item,spec.hand_difference)
    elif spec.origin is ScalarAmountOrigin.EVENT_AMOUNT:
        context = item.context.get("event_context", item.context)
        amount = context.get("amount") if isinstance(context, Mapping) else None
    elif spec.origin is ScalarAmountOrigin.PUBLIC_AGGREGATE:
        from .public_characteristic_aggregates import current_public_characteristic_aggregate
        try:
            amount = current_public_characteristic_aggregate(host, item.controller, spec.aggregate)
        except ValueError as exc:
            raise PublicQueryAmountError(str(exc)) from exc
    elif spec.origin is ScalarAmountOrigin.CONTROLLER_COMMANDER_CAST_COUNT:
        from .commander_cast_counts import controller_commander_cast_count
        amount = controller_commander_cast_count(host.state, item.controller)
    elif spec.reads_source_mutation_count:
        amount = _reference(host, item, spec, use_phased_source_lki=True).get("mutation_count")
        if type(amount) is not int or amount < 0:
            raise PublicQueryAmountError("The original source mutation count is unavailable or malformed")
    elif spec.origin is ScalarAmountOrigin.HISTORY:
        amount = _history_amount(host, item, spec.history_fact)
    elif spec.origin is ScalarAmountOrigin.HISTORY_COUNT:
        from .public_history_query_model import public_history_query_value

        amount = public_history_query_value(host.state, item.controller, spec.history_count)
    else:
        count_kinds = spec.schema_version == SOURCE_COUNTER_KIND_COUNT_VERSION
        if spec.reads_source_counters and spec.binding_id is not None:
            program = host.semantics.get(item.semantic_key) if item.semantic_key else None
            if program is not None and _shared_counter_result_amount(program.effects) == value:
                use_phased_source_lki = True
        reference = _reference(host, item, spec, use_phased_source_lki=use_phased_source_lki or count_kinds)
        if spec.reads_source_counters:
            counters=reference.get('counters')
            if not isinstance(counters,Mapping) or any(type(v) is not int or v<0 for v in counters.values()):
                raise PublicQueryAmountError("Scalar source counter snapshot is unavailable or malformed")
            if count_kinds:
                amount = len({normalized_counter_name(name) for name, value in counters.items() if value > 0})
            else:
                amount = counters.get(spec.counter_name, 0)
        else:
            amount = reference.get(spec.characteristic)
        if spec.characteristic == "mana_value" and type(amount) is float and amount.is_integer():
            amount = int(amount)
    if spec.origin is ScalarAmountOrigin.TARGET_GRAVEYARD_MANA_VALUE and type(amount) is int and amount < 0:
        raise PublicQueryAmountError('Intrinsic graveyard mana value cannot be negative')
    if type(amount) is not int:
        raise PublicQueryAmountError("Scalar information is unavailable or malformed")
    # Ordinary result amounts, including +X modifiers, use CR 107.1b's zero.
    amount = max(0, amount)
    if cache is not None:
        cache[spec.binding_id] = {"identity": identity, "value": amount}
    return spec.coefficient * amount
