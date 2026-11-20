from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from .errors import GameRuleError
from .mana_spend_rules import paid_mana_protects_cast
from .model import StackItem
from .replacement.immutable import thaw_value
from .spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY, SpellManaSpentCounts
from .rules.next_cast_counter_protection import announced_cast_is_protected
from .semantic_runtime.static_cast_rules import static_stack_item_uncounterable
from .zone_trigger_events import ZoneTransitionKind, counter_destination_fields
from .combat_entry_activations import cleanup_ninjutsu_reveal
from .effect_runtime.hand_inspection import cleanup_stack_cost_hand_reveals
from .turn_history import current_turn_history_events
from .turn_history_model import CAST_CREATURE_COUNTERED_EVENT, TurnHistoryEvent, TurnHistoryEventKind


INTRINSIC_COUNTER_PROHIBITION_CAPABILITY = (
    "stack.counter.prohibition.intrinsic"
)

NO_MANA_SPENT_COUNTER_FIELD = "counter_if_no_mana_spent_version"
ANNOUNCED_X_COUNTER_FIELD = "counter_if_mana_value_equals_announced_x"
_NO_MANA_SPENT_COUNTER = {
    "op": "counter_stack_target", "stack": "$target.0", NO_MANA_SPENT_COUNTER_FIELD: 1,
}
_ORDINARY_SPELL_TARGET = {
    "zones": ["stack"], "categories": ["spell"], "source_exclusion": True, "count": 1,
}


def _validate_no_mana_spent_counter(effect):
    if (
        not isinstance(effect, Mapping) or effect != _NO_MANA_SPENT_COUNTER
        or type(effect.get(NO_MANA_SPENT_COUNTER_FIELD)) is not int
    ):
        raise ValueError("Conditional payment counter requires one exact original spell target and version")


def no_mana_spent_counter_capabilities(effect, target_schema):
    _validate_no_mana_spent_counter(effect)
    if (
        not isinstance(target_schema, Mapping) or thaw_value(target_schema) != _ORDINARY_SPELL_TARGET
        or type(target_schema.get("count")) is not int or target_schema.get("source_exclusion") is not True
    ):
        raise ValueError("Conditional payment counter requires the ordinary any-spell target")
    return MANA_SPENT_COUNTS_CAPABILITY, "stack.counter.effect", "target.revalidate_resolution"


def no_mana_spent_counter_runtime_dependencies(program):
    from .rules.effect_shape_walkers import nested_effects

    effects = (*program.effects, *getattr(program, "handlers", ()))
    if not any(NO_MANA_SPENT_COUNTER_FIELD in effect for effect in nested_effects(effects)):
        return ()
    if (
        (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:")
        or len(program.effects) != 1 or getattr(program, "handlers", ())
    ):
        raise ValueError("Conditional payment counter requires its complete spell resolution")
    return no_mana_spent_counter_capabilities(program.effects[0], program.target_schema)


def stack_spell_has_no_mana_spent(state, item):
    if item.kind == "spell_copy":
        return item.mana_spent_counts is None
    receipt = item.mana_spent_counts
    card = state.cards.get(item.card_object_id)
    return (
        item.kind == "spell" and isinstance(receipt, SpellManaSpentCounts)
        and card is not None and card.zone == "stack"
        and (card.object_id, card.logical_object_id) == (receipt.object_id, receipt.logical_object_id)
        and not any(receipt.amounts)
    )


def bind_no_mana_spent_counter(state, raw_effect, effect):
    if NO_MANA_SPENT_COUNTER_FIELD not in raw_effect:
        return effect
    try:
        _validate_no_mana_spent_counter(raw_effect)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    ref = effect.get("stack")
    target = next((item for item in state.stack if item.ref == ref), None)
    if target is None or not stack_spell_has_no_mana_spent(state, target):
        return None
    return {field: value for field, value in effect.items() if field != NO_MANA_SPENT_COUNTER_FIELD}


def _validate_announced_x_counter(effect):
    from .query_effect_amount_model import CastXAmountSpec

    if (not isinstance(effect, Mapping) or set(effect) != {"op", "stack", ANNOUNCED_X_COUNTER_FIELD}
            or effect.get("op") != "counter_stack_target" or effect.get("stack") != "$target.0"
            or CastXAmountSpec.from_dict(effect[ANNOUNCED_X_COUNTER_FIELD]) != CastXAmountSpec()):
        raise ValueError("Conditional mana-value counter requires the exact announced X and one spell target")


def announced_x_counter_capabilities(effect, target_schema):
    from .query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY

    _validate_announced_x_counter(effect)
    if (not isinstance(target_schema, Mapping) or thaw_value(target_schema) != _ORDINARY_SPELL_TARGET
            or type(target_schema.get("count")) is not int or target_schema.get("source_exclusion") is not True):
        raise ValueError("Announced-X counter requires the ordinary any-spell target")
    return DECLARED_AMOUNT_CAPABILITY, "stack.counter.effect", "target.revalidate_resolution"


def announced_x_counter_runtime_dependencies(program):
    from .rules.effect_shape_walkers import nested_effects

    effects = (*program.effects, *getattr(program, "handlers", ()))
    if not any(ANNOUNCED_X_COUNTER_FIELD in effect for effect in nested_effects(effects)):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:") or len(program.effects) != 1
            or getattr(program, "handlers", ())):
        raise ValueError("Announced-X counter requires its complete spell resolution")
    return announced_x_counter_capabilities(program.effects[0], program.target_schema)


def bind_announced_x_counter(host, item, raw_effect, effect):
    if ANNOUNCED_X_COUNTER_FIELD not in raw_effect:
        return effect
    from .rules.stack_controller_payment_cost import _target_spell_mana_value
    from .semantic_runtime.query_effect_amounts import resolve_cast_x_amount

    try:
        _validate_announced_x_counter(raw_effect)
        amount = resolve_cast_x_amount(raw_effect[ANNOUNCED_X_COUNTER_FIELD], item)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    ref = effect.get("stack")
    target = next((value for value in host.state.stack if value.ref == ref), None)
    if target is None or target.kind not in {"spell", "spell_copy"}:
        return None
    try:
        target_mana_value = _target_spell_mana_value(host, target)
    except ValueError:
        return None
    if target_mana_value != amount:
        return None
    return {field: value for field, value in effect.items() if field != ANNOUNCED_X_COUNTER_FIELD}


class StackCounterHost(Protocol):
    state: Any
    semantics: Any

    def move_card(self, object_id: str, zone: str, **kwargs: Any) -> Any: ...

    def _increment_optimization(self, seat: str, key: str) -> None: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(self, type_line: str) -> tuple[set[str], set[str], set[str]]: ...

    def _record_turn_history(self, kind: TurnHistoryEventKind, **facts: Any) -> None: ...

    def _log(
        self,
        actor: str | None,
        code: str,
        summary: str,
        details: Any = None,
        **kwargs: Any,
    ) -> None: ...


def oracle_has_intrinsic_counter_prohibition(
    semantics: Any,
    oracle_id: str,
    *,
    current_trusted: Callable[[Any], bool],
) -> bool:
    """Return the pinned trusted CardProgram declaration for one Oracle ID."""

    return any(current_intrinsic_counter_prohibitions(semantics, oracle_id, current_trusted=current_trusted))


def current_intrinsic_counter_prohibitions(
    semantics: Any,
    oracle_id: str,
    *,
    current_trusted: Callable[[Any], bool],
):
    for program in semantics.programs_for_oracle(oracle_id):
        if (
            program.active_zone == "stack" and program.event == "continuous"
            and INTRINSIC_COUNTER_PROHIBITION_CAPABILITY in program.capability_dependencies
            and current_trusted(program)
        ):
            yield program


def stack_item_can_be_countered(
    host: StackCounterHost,
    item: StackItem,
) -> bool:
    if item.context.get("cant_be_countered") or announced_cast_is_protected(item) or paid_mana_protects_cast(item):
        return False
    if static_stack_item_uncounterable(host, item):
        return False
    if item.kind in {"spell", "spell_copy"} and host.state.players[
        item.controller
    ].stats.get(
        "spells_cant_be_countered_until_end"
    ):
        return False
    if item.card_object_id:
        card = host.state.cards[item.card_object_id]
        if card.annotations.get("cant_be_countered"):
            return False
    return True


def _prepare_counter_destination(host, item, value, selections):
    from .entry_counters import capture_prospective_entry_characteristics
    from .semantic_runtime.zone_replacements import prepare_zone_change_replacement

    if item.kind not in {"spell", "spell_copy"}:
        raise GameRuleError("Only a countered spell can move to an alternate destination")
    zone, position = counter_destination_fields(value)
    card = host.state.cards.get(item.card_object_id or "")
    if card is None or card.zone != "stack":
        return zone, {}
    characteristics, _ = capture_prospective_entry_characteristics(
        host, card=card, enter_face=None, battlefield_entry=False,
    )
    prepared = prepare_zone_change_replacement(
        host, card, zone, entry_characteristics=characteristics,
        transition_kind=ZoneTransitionKind.COUNTERED_SPELL, selections=selections,
        error_type=GameRuleError,
    )
    return zone, {"position": position, "prepared_replacement": prepared}


def _creature_cast_before_counter(
    host: StackCounterHost, item: StackItem, source_controller: str | None,
) -> TurnHistoryEvent | None:
    history = getattr(host.state, "turn_history", None)
    if (getattr(history, "creature_spell_counter_history_version", None) != 1
            or item.kind != "spell" or source_controller not in host.state.players):
        return None
    card = host.state.cards.get(item.card_object_id or "")
    if card is None or card.zone != "stack":
        return None
    casts = current_turn_history_events(history, turn_sequence=host.state.turn_sequence, kind="spell_cast")
    cast = next((event for event in casts if event.object_incarnation == card.logical_object_id
                 and event.actor in host.state.players), None)
    if cast is None:
        return None
    effective = host._effective_card_data(card)
    types, _, _ = host._type_parts(str(effective.get("type_line") or ""))
    return cast if "creature" in types else None


def counter_stack_item(
    host: StackCounterHost,
    value: str,
    *,
    destination: str = "graveyard",
    reason: str = "countered",
    as_rule: bool = False,
    countered_by: str | None = None,
    source_controller: str | None = None,
    counter_destination: str | None = None,
    replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
) -> StackItem:
    item = next(
        (
            candidate
            for candidate in host.state.stack
            if candidate.ref == value or candidate.stack_id == value
        ),
        None,
    )
    if item is None:
        raise GameRuleError(f"No stack object {value}")
    if not as_rule and not stack_item_can_be_countered(host, item):
        host._log(
            countered_by,
            "stack.counter.failed",
            f"{item.ref} {item.label} could not be countered.",
            {
                "stack": item.ref,
                "reason": reason,
                "cant_be_countered": True,
            },
            importance=2,
        )
        return item
    move_options = {}
    if counter_destination is not None:
        destination, move_options = _prepare_counter_destination(
            host, item, counter_destination, replacement_selections,
        )
    from .rules.event_spell_copy import pin_event_spell_copy_departure
    pin_event_spell_copy_departure(host,item)
    source_controller = source_controller if source_controller is not None else countered_by
    creature_cast = None if as_rule else _creature_cast_before_counter(host, item, source_controller)
    host.state.stack.remove(item)
    if creature_cast is not None:
        host._record_turn_history(
            CAST_CREATURE_COUNTERED_EVENT, actor=creature_cast.actor,
            object_incarnation=creature_cast.object_incarnation, target=source_controller,
            target_kind="player",
        )
    cleanup_ninjutsu_reveal(host, item)
    cleanup_stack_cost_hand_reveals(host,item)
    if item.card_object_id:
        card = host.state.cards[item.card_object_id]
        if card.zone == "stack":
            moved = host.move_card(
                card.object_id,
                destination,
                reason=reason,
                log=False,
                semantic_events=True,
                transition_kind=ZoneTransitionKind.COUNTERED_SPELL,
                **move_options,
            )
            if counter_destination is not None:
                destination = moved.zone
    telemetry_seat = (
        countered_by
        if countered_by in host.state.players
        else item.controller
    )
    host._increment_optimization(
        telemetry_seat,
        (
            "spells_countered_by_rules"
            if as_rule
            else "spells_countered_by_effect"
        ),
    )
    host._log(
        countered_by,
        "stack.counter",
        f"{item.ref} {item.label} was countered.",
        {
            "stack": item.ref,
            "destination": destination,
            "reason": reason,
            "counter_kind": "rules" if as_rule else "effect",
        },
        importance=2,
    )
    return item


__all__ = [
    "INTRINSIC_COUNTER_PROHIBITION_CAPABILITY",
    "StackCounterHost",
    "counter_stack_item",
    "current_intrinsic_counter_prohibitions",
    "oracle_has_intrinsic_counter_prohibition",
    "stack_item_can_be_countered",
]
