from __future__ import annotations

import copy
from typing import Any, Mapping, Protocol, Sequence

from .characteristic_evaluation import type_parts
from .replacement import (
    CreateAffectedObjectCounter,
    ReplacementClass,
    ReplacementEffect,
)
from .entry_counter_model import (
    DynamicEntryCounterAmountSpec,
    DynamicEntryCounterValueSource,
    EntryCounterError,
    EffectEntryCounter,
    IntrinsicEntryCounter,
    intrinsic_entry_counters,
)
from .turn_history import current_turn_history_events


class EntryCharacteristicsQuery(Protocol):
    def _effective_card_data(
        self,
        card: Any,
        *,
        printed_entry_characteristics: bool = False,
    ) -> Mapping[str, Any]: ...


def dynamic_entry_counter_amount(
    host: Any,
    *,
    card: Any,
    destination_controller: str,
    amount_spec: DynamicEntryCounterAmountSpec,
    mana_colors_spent: Sequence[str] = (),
) -> int:
    """Resolve one dynamic entry amount before replacement ordering."""

    if not destination_controller:
        raise EntryCounterError(
            "Dynamic entry counter amounts require a destination controller"
        )
    stack_items = tuple(
        item
        for item in host.state.stack
        if item.kind in {"spell", "spell_copy"}
        and item.card_object_id == card.object_id
    )
    if card.zone == "stack" and len(stack_items) != 1:
        raise EntryCounterError(
            "A cast dynamic entry counter requires one current stack object"
        )
    item = stack_items[0] if stack_items else None
    was_cast = item is not None and item.kind == "spell"
    source = amount_spec.value_source
    if source is DynamicEntryCounterValueSource.CAST_X:
        value = int(item.x_value or 0) if item is not None else 0
    elif source is DynamicEntryCounterValueSource.MANA_COLORS_SPENT:
        value = len(tuple(mana_colors_spent)) if was_cast else 0
    elif source is DynamicEntryCounterValueSource.CAST_FROM_HAND:
        value = int(
            was_cast and item.context.get("cast_origin") == "hand"
        )
    elif source is DynamicEntryCounterValueSource.MANA_WAS_SPENT:
        raw = item.context.get("mana_spent_total", 0) if was_cast else 0
        if type(raw) is not int or raw < 0:
            raise EntryCounterError(
                "Dynamic entry mana-spent provenance is malformed"
            )
        value = int(raw > 0)
    elif source is DynamicEntryCounterValueSource.PUBLIC_QUERY:
        from .dynamic_characteristics import query_characteristic_count

        assert amount_spec.quantity is not None
        prospective_source = copy.copy(card)
        prospective_source.controller = destination_controller
        value = query_characteristic_count(
            host,
            prospective_source,
            amount_spec.quantity,
        )
    else:
        events = {
            kind: current_turn_history_events(
                host.state.turn_history,
                turn_sequence=host.state.turn_sequence,
                kind=kind,
            )
            for kind in (
                "creature_attacked",
                "creature_died",
                "player_lost_life",
                "spell_cast",
            )
        }
        if source is DynamicEntryCounterValueSource.CONTROLLER_ATTACKED:
            value = int(
                any(
                    event.actor == destination_controller
                    for event in events["creature_attacked"]
                )
            )
        elif (
            source
            is DynamicEntryCounterValueSource.CONTROLLER_OTHER_SPELLS_CAST
        ):
            current_incarnation = (
                card.logical_object_id if was_cast else None
            )
            value = sum(
                event.actor == destination_controller
                and event.object_incarnation != current_incarnation
                for event in events["spell_cast"]
            )
        elif source is DynamicEntryCounterValueSource.CONTROLLER_SPELLS_CAST:
            value = sum(
                event.actor == destination_controller
                for event in events["spell_cast"]
            )
        elif source is DynamicEntryCounterValueSource.CREATURES_DIED:
            value = len(events["creature_died"])
        elif source is DynamicEntryCounterValueSource.OTHER_SPELLS_CAST:
            current_incarnation = (
                card.logical_object_id if was_cast else None
            )
            value = sum(
                event.object_incarnation != current_incarnation
                for event in events["spell_cast"]
            )
        elif source is DynamicEntryCounterValueSource.OPPONENTS_LIFE_LOST:
            opponents = set(host.active_seats) - {destination_controller}
            value = sum(
                event.amount
                for event in events["player_lost_life"]
                if event.target in opponents
            )
        else:
            raise EntryCounterError(
                "Dynamic entry counter value source is unsupported"
            )
    return amount_spec.amount(value)


def capture_prospective_entry_characteristics(
    host: EntryCharacteristicsQuery,
    *,
    card: Any,
    enter_face: str | None,
) -> tuple[Mapping[str, Any], str]:
    """Snapshot printed entry characteristics before replacement ordering."""

    prospective_card = copy.deepcopy(card)
    if enter_face is not None:
        prospective_card.active_face = enter_face
    characteristics = host._effective_card_data(
        prospective_card,
        printed_entry_characteristics=True,
    )
    return characteristics, str(characteristics.get("type_line") or "")


def intrinsic_entry_counter_effects(
    *,
    object_ref: str,
    destination_controller: str,
    counters: Sequence[IntrinsicEntryCounter],
) -> tuple[ReplacementEffect, ...]:
    """Lower intrinsic instructions to mandatory self-replacement effects."""

    if not object_ref or not destination_controller:
        raise EntryCounterError(
            "Entry counter effects require object and controller identity"
        )
    effects: list[ReplacementEffect] = []
    for sequence, counter in enumerate(counters):
        if not isinstance(counter, IntrinsicEntryCounter):
            raise EntryCounterError(
                "Entry counter effects require typed counter instructions"
            )
        if counter.amount == 0:
            continue
        source_ref = f"rule:{counter.rule_id}:{object_ref}"
        effects.append(
            ReplacementEffect(
                effect_id=(
                    "replacement.intrinsic-entry-counter:"
                    f"{object_ref}:{counter.counter_name}:{counter.rule_id}"
                ),
                source_id=source_ref,
                event_kind="zone.change",
                replacement_class=ReplacementClass.SELF_REPLACEMENT,
                conditions={
                    "destination": {"eq": "battlefield"},
                    "object_ref": {"eq": object_ref},
                    "object_types": {
                        "contains": counter.required_type,
                    },
                },
                operations=(
                    CreateAffectedObjectCounter(
                        counter_name=counter.counter_name,
                        amount=counter.amount,
                        placing_player=destination_controller,
                        source_ref=source_ref,
                        sequence=sequence,
                    ),
                ),
                label=(
                    f"{object_ref}: enter with {counter.amount} "
                    f"{counter.counter_name} counter(s)"
                ),
            )
        )
    return tuple(effects)


def effect_entry_counter_effects(
    *,
    object_ref: str,
    counters: Sequence[EffectEntryCounter],
) -> tuple[ReplacementEffect, ...]:
    """Lower effect-generated entry counters into the same event tree."""

    if type(object_ref) is not str or not object_ref:
        raise EntryCounterError(
            "Effect entry counter effects require object identity"
        )
    effects: list[ReplacementEffect] = []
    for sequence, counter in enumerate(counters):
        if not isinstance(counter, EffectEntryCounter):
            raise EntryCounterError(
                "Effect entry counters require typed instructions"
            )
        effects.append(
            ReplacementEffect(
                effect_id=(
                    "replacement.effect-entry-counter:"
                    f"{object_ref}:{counter.source_ref}:{sequence}:"
                    f"{counter.counter_name}:{counter.rule_id}"
                ),
                source_id=counter.source_ref,
                event_kind="zone.change",
                replacement_class=ReplacementClass.SELF_REPLACEMENT,
                conditions={
                    "destination": {"eq": "battlefield"},
                    "object_ref": {"eq": object_ref},
                },
                operations=(
                    CreateAffectedObjectCounter(
                        counter_name=counter.counter_name,
                        amount=counter.amount,
                        placing_player=counter.placing_player,
                        source_ref=counter.source_ref,
                        sequence=sequence,
                    ),
                ),
                label=(
                    f"{object_ref}: enter with {counter.amount} "
                    f"{counter.counter_name} counter(s)"
                ),
            )
        )
    return tuple(effects)


def validate_battle_entry_protector(
    *,
    card_types: Sequence[str],
    subtypes: Sequence[str],
    controller: str,
    supplied_protector: str | None,
    active_seats: Sequence[str],
) -> str | None:
    """Validate the represented ordinary Battle protector assignment."""

    types = {str(value).casefold() for value in card_types}
    if "battle" not in types:
        return None
    normalized_subtypes = {str(value).casefold() for value in subtypes}
    if "siege" in normalized_subtypes:
        if (
            supplied_protector not in active_seats
            or supplied_protector == controller
        ):
            raise EntryCounterError(
                "A Siege must enter protected by one of its controller's opponents"
            )
        return supplied_protector
    if normalized_subtypes:
        raise EntryCounterError(
            "The protector predicate for Battle type(s) "
            f"{sorted(normalized_subtypes)} is not compiled"
        )
    return controller


def prospective_battle_entry_protector(
    *,
    destination: str,
    entry_characteristics: Mapping[str, Any],
    controller: str,
    supplied_protector: str | None,
    active_seats: Sequence[str],
    error_type: type[Exception],
) -> str | None:
    """Validate the Battle protector against prospective entry data."""

    if destination != "battlefield":
        return None
    card_types, subtypes, _ = type_parts(
        str(entry_characteristics.get("type_line") or "")
    )
    try:
        return validate_battle_entry_protector(
            card_types=tuple(sorted(card_types)),
            subtypes=tuple(sorted(subtypes)),
            controller=controller,
            supplied_protector=supplied_protector,
            active_seats=active_seats,
        )
    except EntryCounterError as exc:
        raise error_type(str(exc)) from exc


def mark_intrinsic_entry_counters_initialized(
    card: Any,
    *,
    destination: str,
    destination_type_line: str,
) -> None:
    """Retain zero-loyalty SBA eligibility after entry counters leave."""

    if destination != "battlefield":
        return
    card_types, _subtypes, _supertypes = type_parts(destination_type_line)
    if "planeswalker" in card_types:
        card.annotations["loyalty_initialized"] = True


__all__ = [
    "capture_prospective_entry_characteristics",
    "dynamic_entry_counter_amount",
    "EntryCounterError",
    "EntryCharacteristicsQuery",
    "EffectEntryCounter",
    "IntrinsicEntryCounter",
    "effect_entry_counter_effects",
    "intrinsic_entry_counter_effects",
    "intrinsic_entry_counters",
    "mark_intrinsic_entry_counters_initialized",
    "prospective_battle_entry_protector",
    "validate_battle_entry_protector",
]
