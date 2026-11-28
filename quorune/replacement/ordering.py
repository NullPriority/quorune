from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence
from dataclasses import replace

from .applicability import canonical_effects, replacement_choice
from .application import (
    apply_replacement,
    canonical_replacement_selection,
)
from .model import (
    PreventionAllocationChoice,
    ReplaceableEvent,
    ReplacementBatchChoice,
    ReplacementBatchProgress,
    ReplacementChoice,
    ReplacementEffect,
    ReplacementEffectError,
    ReplacementEventBatch,
    ReplacementSelection,
    ReplacementTreeChoice,
)
from .immutable import thaw_value
from ..entry_designations import entry_card_name_operation
from .operations import (
    AmplifyEntryOperation,
    DevourEntryOperation,
    ENTRY_OBJECT_SELECTION_OPERATIONS,
    GraveyardCopyEntryOperation,
    LinkedGraveyardEntryOperation,
    PreventUsingShield,
)
from .counted_entry_counters import (
    ENTRY_COUNTER_TRANSFER_OPERATIONS, ENTRY_COUNTER_TRANSFER_RESULT, ChosenTransferEntryCounters,
    TransferEntryCounters, entry_counter_transfer_receipt,
)
from .entry_reservations import share_entry_reservations
from .entry_counter_types import project_entry_time_counter_types
from .counter_instruction_grouping import (
    combine_counter_instruction_choices,
    direct_counter_instruction_indices,
    entry_counter_instruction_indices,
)
from .damage_redirection import (
    finite_damage_redirection,
    split_finite_redirection,
    exclusive_damage_scope_effects,
)


class ReplacementChoiceRequired(ReplacementEffectError):
    """Raised only at an engine suspension boundary before event commit."""

    def __init__(
        self,
        *,
        batch: ReplacementEventBatch,
        effects: Sequence[ReplacementEffect],
        pending: ReplacementBatchChoice,
    ) -> None:
        super().__init__(
            f"{pending.choice.chooser} must choose a replacement effect"
        )
        self.batch = batch
        self.effects = canonical_effects(effects)
        self.pending = pending


def replacement_tree_choice(
    event: ReplaceableEvent,
    effects: Iterable[ReplacementEffect],
) -> ReplacementTreeChoice | None:
    """Return the first choice in containing-before-contained order."""

    all_effects = canonical_effects(effects)

    def visit(
        current: ReplaceableEvent, path: tuple[int, ...]
    ) -> ReplacementTreeChoice | None:
        from .tribute import pending_tribute_entry_choice
        for index,child in enumerate(current.children):
            if pending_tribute_entry_choice(child):
                choice=replacement_choice(child,all_effects)
                if choice is not None:
                    return ReplacementTreeChoice(path=(*path,index),choice=choice)
        from .entry_counter_ordering import entry_counter_order_effects
        from .entry_counter_types import redirected_battlefield_entry_counter

        choice = replacement_choice(current, entry_counter_order_effects(current, all_effects))
        if choice is not None:
            return ReplacementTreeChoice(path=path, choice=choice)
        for index, child in enumerate(current.children):
            if redirected_battlefield_entry_counter(current, child):
                continue
            nested = visit(child, (*path, index))
            if nested is None:
                continue
            if nested.path == (*path, index):
                siblings = entry_counter_instruction_indices(current, child, all_effects)
                members = []
                for sibling in siblings:
                    candidate = current.children[sibling]
                    if redirected_battlefield_entry_counter(current, candidate):
                        continue
                    applicable = replacement_choice(candidate, entry_counter_order_effects(candidate, all_effects))
                    if applicable is not None:
                        members.append(((*path, sibling), applicable))
                return combine_counter_instruction_choices(nested, members)
            return nested
        return None

    return visit(event, ())


def _replace_event_at_path(
    event: ReplaceableEvent,
    path: tuple[int, ...],
    replacement: ReplaceableEvent,
) -> ReplaceableEvent:
    if not path:
        return project_entry_time_counter_types(replacement)
    index = path[0]
    if index < 0 or index >= len(event.children):
        raise ReplacementEffectError(
            "Replacement event path is no longer valid"
        )
    children = list(event.children)
    children[index] = _replace_event_at_path(
        children[index], path[1:], replacement
    )
    return project_entry_time_counter_types(ReplaceableEvent(
        event_id=event.event_id,
        kind=event.kind,
        affected_player=event.affected_player,
        affected_object=event.affected_object,
        payload=event.payload,
        applied_effects=event.applied_effects,
        children=tuple(children),
        entry_scope=event.entry_scope,
    ))


def apply_tree_replacement(
    event: ReplaceableEvent,
    effects: Iterable[ReplacementEffect],
    pending: ReplacementTreeChoice,
    selected_effect_id: str | None,
    *, objects: tuple[str,...] | None = None, card_name: str | None = None,
) -> ReplaceableEvent:
    all_effects = canonical_effects(effects)
    current = replacement_tree_choice(event, all_effects)
    if current is None or current != pending:
        raise ReplacementEffectError(
            "Replacement tree choice is stale or out of order"
        )
    from .entry_counter_ordering import entry_counter_order_effects
    if pending.counter_instruction_members:
        result = event
        for path, changed in _counter_instruction_updates(
            pending, selected_effect_id, all_effects, objects=objects, card_name=card_name,
        ):
            result = _replace_event_at_path(result, path, changed)
        return result
    adapted=entry_counter_order_effects(pending.choice.event, all_effects)
    changed=apply_replacement(pending.choice,adapted,selected_effect_id,objects=objects,card_name=card_name)
    return _replace_event_at_path(event, pending.path, changed)


def _counter_instruction_updates(pending, selected_effect_id, effects, *, objects=None, card_name=None):
    from .entry_counter_ordering import entry_counter_order_effects

    choice = canonical_replacement_selection(pending.choice, selected_effect_id)
    if objects is not None or card_name is not None:
        raise ReplacementEffectError("A counter instruction accepts one replacement choice")
    for path, member in pending.counter_instruction_members:
        if choice in member.legal_selections:
            adapted = entry_counter_order_effects(member.event, effects)
            yield path, apply_replacement(member, adapted, choice)


def _available_entry_counter_transfers(batch, effects):
    if not batch.journal:
        return effects
    committed = tuple(
        entry_counter_transfer_receipt(event.payload[ENTRY_COUNTER_TRANSFER_RESULT])
        for event in batch.events if ENTRY_COUNTER_TRANSFER_RESULT in event.payload
    )
    if not committed:
        return effects
    claimed = {row.key for receipt in committed for row in receipt.removals}
    all_permanents_consumed = any(isinstance(receipt, TransferEntryCounters) for receipt in committed)
    available = []
    for effect in effects:
        if len(effect.operations) != 1 or not isinstance(effect.operations[0], ENTRY_COUNTER_TRANSFER_OPERATIONS):
            available.append(effect)
            continue
        operation = effect.operations[0]
        if all_permanents_consumed and isinstance(operation, TransferEntryCounters):
            continue
        removals = tuple(row for row in operation.removals if row.key not in claimed)
        if removals:
            available.append(effect if removals == operation.removals else replace(
                effect, operations=(replace(operation, removals=removals),),
            ))
    return tuple(available)


def _available_batch_effects(batch, effects):
    from .chosen_basic_land_entry import available_basic_land_payments

    eligible = available_basic_land_payments(batch, _available_entry_counter_transfers(batch, effects))
    if not batch.journal:
        return eligible
    eligible = exclusive_damage_scope_effects(batch.journal, eligible)
    used = {selection.effect_id for selection in batch.journal}
    return tuple(effect for effect in eligible if effect.effect_id not in used or not (
        finite_damage_redirection(effect) is not None
        or any(isinstance(operation, PreventUsingShield) for operation in effect.operations)
    ))


def next_batch_replacement_choice(
    batch: ReplacementEventBatch,
    effects: Iterable[ReplacementEffect],
) -> ReplacementBatchChoice | None:
    from .chosen_basic_land_entry import pending_basic_land_payment
    from .tribute import pending_tribute_entry_choice
    all_effects = canonical_effects(effects)
    available_effects = _available_batch_effects(batch, all_effects)
    order = {seat: index for index, seat in enumerate(batch.apnap_order)}
    candidates: list[tuple[int, str, int, ReplacementTreeChoice]] = []
    for event_index, event in enumerate(batch.events):
        pending = replacement_tree_choice(event, available_effects)
        if pending is None:
            continue
        chooser_index = order.get(pending.choice.chooser)
        if chooser_index is None:
            raise ReplacementEffectError(
                "Nested replacement chooser is absent from APNAP order"
            )
        candidates.append(
            (chooser_index, event.event_id, event_index, pending)
        )
    if not candidates:
        return None
    chooser_index, event_id, event_index, pending = min(
        candidates,
        key=lambda value: (
            not (pending_tribute_entry_choice(value[3].choice.event)
                 or pending_basic_land_payment(value[3].choice.event)),
            value[0],
            value[1],
            value[3].path,
            value[2],
        ),
    )
    one_shot_ids = {
        effect.effect_id
        for effect in available_effects
        if finite_damage_redirection(effect) is not None or any(
            isinstance(operation, PreventUsingShield)
            and operation.remaining is None
            and operation.consume_on_application
            for operation in effect.operations
        )
    }
    same_chooser = [
        value for value in candidates if value[0] == chooser_index
    ]
    shared_one_shot = {
        effect_id
        for effect_id in one_shot_ids
        if sum(
            effect_id in candidate[3].choice.options
            for candidate in same_chooser
        )
        > 1
    }
    event_order_options = tuple(
        sorted(
            candidate[1]
            for candidate in same_chooser
            if shared_one_shot.intersection(candidate[3].choice.options)
        )
    )
    by_id = {effect.effect_id: effect for effect in all_effects}
    shared_entry_order = any(
        sum(any(isinstance(operation, operation_type) for effect_id in value[3].choice.options
                for operation in by_id[effect_id].operations) for value in same_chooser) > 1
        for operation_type in (DevourEntryOperation, ENTRY_COUNTER_TRANSFER_OPERATIONS)
    )
    if shared_entry_order:
        event_order_options = tuple(sorted({value[1] for value in same_chooser}))
    if pending_basic_land_payment(pending.choice.event):
        shared_entry_order = False
        event_order_options = ()
    if not pending.path and not event_order_options:
        indices = direct_counter_instruction_indices(batch.events, pending.choice.event, available_effects)
        if indices:
            members = [
                ((value[2],), value[3].choice) for value in same_chooser
                if not value[3].path and value[2] in indices
            ]
            pending = combine_counter_instruction_choices(pending, members)
    result = _batch_choice_for_event(
        batch,
        all_effects=all_effects,
        available_effects=available_effects,
        event_index=event_index,
        event_id=event_id,
        pending=pending,
        event_order_options=event_order_options,
    )
    if shared_entry_order or any(finite_damage_redirection(by_id[effect_id]) is not None for effect_id in shared_one_shot):
        result = replace(result, event_choices=tuple(
            (value[1], value[3].choice) for value in same_chooser if value[1] in event_order_options
        ))
    return result


def _batch_choice_for_event(
    batch: ReplacementEventBatch,
    *,
    all_effects: Sequence[ReplacementEffect],
    available_effects: Sequence[ReplacementEffect],
    event_index: int,
    event_id: str,
    pending: ReplacementTreeChoice,
    event_order_options: tuple[str, ...],
) -> ReplacementBatchChoice:
    base = ReplacementBatchChoice(
        batch_id=batch.batch_id,
        event_index=event_index,
        event_id=event_id,
        tree_choice=pending,
        prior_public_choices=batch.journal,
        event_order_options=event_order_options,
    )
    allocations: list[PreventionAllocationChoice] = []
    effects_by_id = {effect.effect_id: effect for effect in available_effects}
    for effect_id in pending.choice.options:
        effect = effects_by_id[effect_id]
        shield_operations = tuple(
            operation
            for operation in effect.operations
            if isinstance(operation, PreventUsingShield)
        )
        if not shield_operations:
            continue
        if len(effect.operations) != 1 or len(shield_operations) != 1:
            raise ReplacementEffectError(
                "Durable shield effects require one typed prevention operation"
            )
        operation = shield_operations[0]
        matching: list[tuple[str, int, bool]] = []
        candidates = (
            (batch.events[event_index],)
            if operation.remaining is None
            else batch.events
        )
        for candidate in candidates:
            candidate_choice = replacement_tree_choice(
                candidate, available_effects
            )
            if (
                candidate_choice is None
                or candidate_choice.path
                or candidate_choice.choice.chooser != pending.choice.chooser
                or effect_id not in candidate_choice.choice.options
            ):
                continue
            amount = candidate.payload.get("amount")
            if type(amount) is not int or amount < 0:
                raise ReplacementEffectError(
                    "Shield prevention requires nonnegative damage amounts"
                )
            matching.append(
                (
                    candidate.event_id,
                    amount,
                    bool(candidate.payload.get("unpreventable")),
                )
            )
        preventable = [
            amount
            for _event_id, amount, unpreventable in matching
            if not unpreventable and amount > 0
        ]
        required = bool(
            operation.remaining is not None
            and len(preventable) > 1
            and sum(preventable) > operation.remaining
        )
        allocations.append(
            PreventionAllocationChoice(
                effect_id=effect_id,
                shield_id=operation.shield_id,
                available=operation.remaining,
                events=tuple(sorted(matching)),
                allocation_required=required,
            )
        )
    if not allocations:
        return base
    return replace(base, prevention_allocations=tuple(allocations))


def _selection_parts(
    selected: str | None | Mapping[str, Any],
) -> tuple[str | None, Mapping[str, Any] | None, str | None, tuple[str,...] | None, str | None]:
    if selected is None or isinstance(selected, str):
        return selected, None, None, None, None
    if not isinstance(selected, Mapping):
        raise ReplacementEffectError(
            "Replacement selections must be strings or typed objects"
        )
    actual = set(selected)
    allowed_shapes = (
        {"effect_id", "allocation"},
        {"effect_id", "event_id"},
        {"effect_id", "allocation", "event_id"},
        {'effect_id','objects'},
        {'effect_id','objects','event_id'},
        {"effect_id", "card_name"},
        {"effect_id", "card_name", "event_id"},
    )
    if actual not in allowed_shapes:
        raise ReplacementEffectError(
            "Typed replacement selections require effect_id plus allocation or event_id"
        )
    effect_id = selected["effect_id"]
    allocation = selected.get("allocation")
    event_id = selected.get("event_id")
    objects=selected.get('objects')
    if 'objects' in selected and (not isinstance(objects,(list,tuple)) or any(type(v) is not str or not v for v in objects)
            or len(set(objects))!=len(objects)):
        raise ReplacementEffectError('Replacement object selections must be distinct nonempty references')
    if not isinstance(effect_id, str) or not effect_id:
        raise ReplacementEffectError(
            "Typed replacement selections require a stable effect ID"
        )
    if allocation is not None and not isinstance(allocation, Mapping):
        raise ReplacementEffectError(
            "Typed replacement prevention allocation must be an object"
        )
    if event_id is not None and (
        not isinstance(event_id, str) or not event_id
    ):
        raise ReplacementEffectError(
            "Typed replacement event identity must be a nonempty string"
        )
    name = selected.get("card_name")
    if "card_name" in selected and (type(name) is not str or not name or name != name.strip()):
        raise ReplacementEffectError("A replacement card name must be canonical")
    return effect_id, allocation, event_id, tuple(objects) if objects is not None else None, name


def _validated_allocation(
    choice: PreventionAllocationChoice,
    supplied: Mapping[str, Any] | None,
) -> dict[str, int]:
    automatic = choice.automatic_allocation
    if supplied is None:
        if automatic is None:
            raise ReplacementEffectError(
                "The affected player must divide the prevention amount"
            )
        return automatic
    event_amounts = {
        event_id: (amount, unpreventable)
        for event_id, amount, unpreventable in choice.events
    }
    unknown = sorted(str(value) for value in set(supplied) - set(event_amounts))
    if unknown:
        raise ReplacementEffectError(
            "Prevention allocation contains unknown damage event(s): "
            + ", ".join(unknown)
        )
    allocation: dict[str, int] = {}
    for event_id in event_amounts:
        amount = supplied.get(event_id, 0)
        maximum, unpreventable = event_amounts[event_id]
        if type(amount) is not int or amount < 0 or amount > maximum:
            raise ReplacementEffectError(
                "Prevention allocations must be nonnegative and no greater "
                "than the current damage amount"
            )
        if unpreventable and amount:
            raise ReplacementEffectError(
                "Unpreventable damage cannot receive a prevention allocation"
            )
        allocation[event_id] = amount
    preventable_total = sum(
        amount
        for _event_id, amount, unpreventable in choice.events
        if not unpreventable
    )
    required_total = (
        preventable_total
        if choice.available is None
        else min(choice.available, preventable_total)
    )
    if sum(allocation.values()) != required_total:
        raise ReplacementEffectError(
            f"Prevention allocation must total {required_total}"
        )
    return allocation


def _apply_shield_prevention(
    batch: ReplacementEventBatch,
    pending: ReplacementBatchChoice,
    allocation_choice: PreventionAllocationChoice,
    supplied: Mapping[str, Any] | None,
) -> ReplacementEventBatch:
    allocation = _validated_allocation(allocation_choice, supplied)
    matching = {event_id for event_id, _amount, _flag in allocation_choice.events}
    events: list[ReplaceableEvent] = []
    for event in batch.events:
        if event.event_id not in matching:
            events.append(event)
            continue
        payload = thaw_value(event.payload)
        prevented = allocation.get(event.event_id, 0)
        available = int(payload.get("amount", 0))
        if prevented > available:
            raise ReplacementEffectError(
                "Prevention allocation exceeds current damage"
            )
        payload["amount"] = available - prevented
        payload["prevented"] = int(payload.get("prevented", 0)) + prevented
        by_effect = dict(payload.get("prevention_applied") or {})
        by_effect[allocation_choice.effect_id] = prevented
        payload["prevention_applied"] = by_effect
        chooser_history = dict(payload.get("replacement_choosers") or {})
        chooser_history[allocation_choice.effect_id] = (
            pending.choice.chooser
        )
        payload["replacement_choosers"] = chooser_history
        events.append(
            ReplaceableEvent(
                event_id=event.event_id,
                kind=event.kind,
                affected_player=event.affected_player,
                affected_object=event.affected_object,
                payload=payload,
                applied_effects=(
                    *event.applied_effects,
                    allocation_choice.effect_id,
                ),
                children=event.children,
                entry_scope=event.entry_scope,
            )
        )
    journal_allocation = {
        event_id: amount for event_id, amount in allocation.items() if amount
    }
    return ReplacementEventBatch(
        batch_id=batch.batch_id,
        events=tuple(events),
        apnap_order=batch.apnap_order,
        journal=(
            *batch.journal,
            ReplacementSelection(
                event_id=pending.event_id,
                path=pending.path,
                chooser=pending.choice.chooser,
                effect_id=allocation_choice.effect_id,
                allocation=(journal_allocation or None),
            ),
        ),
    )


def apply_batch_replacement(
    batch: ReplacementEventBatch,
    effects: Iterable[ReplacementEffect],
    pending: ReplacementBatchChoice,
    selected_effect_id: str | None | Mapping[str, Any],
    *,
    allow_bound_single_event: bool = False,
) -> ReplacementEventBatch:
    all_effects = canonical_effects(effects)
    current = next_batch_replacement_choice(batch, all_effects)
    if current is None or current != pending:
        raise ReplacementEffectError(
            "Replacement batch choice is stale or violates APNAP order"
        )
    selected_value, supplied_allocation, selected_event_id, supplied_objects, card_name = _selection_parts(
        selected_effect_id
    )
    if (
        allow_bound_single_event
        and not current.event_order_options
        and selected_event_id == current.event_id
    ):
        selected_event_id = None
    if selected_event_id is not None:
        if selected_event_id not in current.event_order_options:
            raise ReplacementEffectError(
                "Selected replacement event is not currently available"
            )
        selected_index = next(
            (
                index
                for index, event in enumerate(batch.events)
                if event.event_id == selected_event_id
            ),
            None,
        )
        if selected_index is None:
            raise ReplacementEffectError(
                "Selected replacement event no longer exists"
            )
        available_effects = _available_batch_effects(batch, all_effects)
        tree_choice = replacement_tree_choice(
            batch.events[selected_index], available_effects
        )
        if (
            tree_choice is None
            or tree_choice.choice.chooser != current.choice.chooser
        ):
            raise ReplacementEffectError(
                "Selected replacement event no longer requires this chooser"
            )
        pending = _batch_choice_for_event(
            batch,
            all_effects=all_effects,
            available_effects=available_effects,
            event_index=selected_index,
            event_id=selected_event_id,
            pending=tree_choice,
            event_order_options=current.event_order_options,
        )
    elif current.event_order_options:
        raise ReplacementEffectError(
            "The affected player must choose the next simultaneous event"
        )
    canonical_selection = canonical_replacement_selection(
        pending.choice, selected_value
    )
    effect=next((e for e in all_effects if e.effect_id==canonical_selection),None)
    object_choice=effect is not None and any(isinstance(op,ENTRY_OBJECT_SELECTION_OPERATIONS) for op in effect.operations)
    name_choice = effect is not None and entry_card_name_operation(effect) is not None
    if name_choice != (card_name is not None):
        raise ReplacementEffectError("The selected replacement and card name must agree")
    if supplied_objects and not object_choice:raise ReplacementEffectError('This replacement does not accept selected objects')
    journal_objects=(supplied_objects or ()) if object_choice else None
    allocation_choice = next(
        (
            value
            for value in pending.prevention_allocations
            if value.effect_id == canonical_selection
        ),
        None,
    )
    if allocation_choice is not None:
        return _apply_shield_prevention(
            batch,
            pending,
            allocation_choice,
            supplied_allocation,
        )
    if supplied_allocation is not None:
        raise ReplacementEffectError(
            "Only a durable prevention shield accepts an allocation"
        )
    events = list(batch.events)
    available_effects = _available_batch_effects(batch, all_effects)
    finite = finite_damage_redirection(effect) if effect is not None else None
    branches = (split_finite_redirection(pending.tree_choice, effect, finite, available_effects)
                if finite is not None else None)
    index = pending.event_index
    if branches is not None:
        events[index:index + 1] = branches
    elif not pending.path and pending.tree_choice.counter_instruction_members:
        for (member_index,), changed in _counter_instruction_updates(
            pending.tree_choice, canonical_selection, available_effects,
            objects=supplied_objects, card_name=card_name,
        ):
            events[member_index] = changed
    else:
        events[index] = apply_tree_replacement(events[index], available_effects, pending.tree_choice,
                                               canonical_selection, objects=supplied_objects, card_name=card_name)
    return ReplacementEventBatch(
        batch_id=batch.batch_id,
        events=share_entry_reservations(events),
        apnap_order=batch.apnap_order,
        journal=(
            *batch.journal,
            ReplacementSelection(
                event_id=pending.event_id,
                path=pending.path,
                chooser=pending.choice.chooser,
                effect_id=canonical_selection,
                objects=journal_objects,
                card_name=card_name,
            ),
        ),
    )


def resolve_replacement_batch(
    batch: ReplacementEventBatch,
    effects: Iterable[ReplacementEffect],
    *,
    selections: Iterable[ReplacementSelection],
) -> ReplacementEventBatch:
    all_effects = canonical_effects(effects)
    current = batch
    for selection in selections:
        if not isinstance(selection, ReplacementSelection):
            raise ReplacementEffectError(
                "Replacement replay selections must be typed"
            )
        pending = next_batch_replacement_choice(current, all_effects)
        if pending is None:
            raise ReplacementEffectError(
                "Replacement replay contains unused selections"
            )
        if (
            (
                selection.event_id != pending.event_id
                and selection.event_id not in pending.event_order_options
            )
            or selection.path != pending.path
            or selection.chooser != pending.choice.chooser
        ):
            raise ReplacementEffectError(
                "Replacement replay selection path or chooser diverged"
            )
        if selection.card_name is not None:
            named = {"effect_id": selection.effect_id, "card_name": selection.card_name}
            if pending.event_order_options:
                named["event_id"] = selection.event_id
            current = apply_batch_replacement(current, all_effects, pending, named)
            continue
        current = apply_batch_replacement(
            current,
            all_effects,
            pending,
            (
                {
                    "effect_id": selection.effect_id,
                    "allocation": thaw_value(selection.allocation),
                    **(
                        {"event_id": selection.event_id}
                        if pending.event_order_options
                        else {}
                    ),
                }
                if selection.allocation is not None
                else (
                    {'effect_id':selection.effect_id,'objects':list(selection.objects),
                        **({'event_id':selection.event_id} if pending.event_order_options else {})}
                    if selection.objects is not None else
                    (
                    {
                        "effect_id": selection.effect_id,
                        "event_id": selection.event_id,
                    }
                    if pending.event_order_options
                    else selection.effect_id
                    )
                )
            ),
        )
    if next_batch_replacement_choice(current, all_effects) is not None:
        raise ReplacementEffectError(
            "Replacement replay selection sequence ended early"
        )
    return current


def advance_replacement_batch(
    batch: ReplacementEventBatch,
    effects: Iterable[ReplacementEffect],
    *,
    selections: Iterable[str | None | Mapping[str, Any]] = (),
    require_all_selections: bool = True,
    allow_bound_single_event: bool = False,
    stop_for_graveyard_copy: bool = False,
) -> ReplacementBatchProgress:
    all_effects = canonical_effects(effects)
    supplied = iter(selections)
    consumed = 0
    current = batch
    while pending := next_batch_replacement_choice(current, all_effects):
        allocation_required = any(
            value.effect_id in pending.choice.options
            and value.allocation_required
            for value in pending.prevention_allocations
        )
        if (
            len(pending.choice.options) == 1
            and not pending.choice.optional_options
            and not allocation_required
            and not pending.event_order_options
            and not any(effect.effect_id == pending.choice.options[0] and entry_card_name_operation(effect)
                        for effect in all_effects)
        ):
            selected: str | None | Mapping[str, Any] = (
                pending.choice.options[0]
            )
        else:
            try:
                selected = next(supplied)
                consumed += 1
            except StopIteration:
                return ReplacementBatchProgress(
                    batch=current,
                    pending=pending,
                    consumed_selections=consumed,
                )
        current = apply_batch_replacement(
            current, all_effects, pending, selected,
            allow_bound_single_event=allow_bound_single_event,
        )
        if stop_for_graveyard_copy:
            from ..rules.graveyard_entry_copy import unprepared_graveyard_copy_occurrences
            from ..rules.linked_graveyard_entry import unprepared_linked_graveyard_entry_choices

            if any(unprepared_graveyard_copy_occurrences(event) or unprepared_linked_graveyard_entry_choices(event)
                   for event in current.events):
                return ReplacementBatchProgress(batch=current,pending=None,consumed_selections=consumed)
    if not require_all_selections:
        return ReplacementBatchProgress(
            batch=current,
            pending=None,
            consumed_selections=consumed,
        )
    try:
        next(supplied)
    except StopIteration:
        return ReplacementBatchProgress(
            batch=current,
            pending=None,
            consumed_selections=consumed,
        )
    raise ReplacementEffectError(
        "Replacement selection sequence contains unused choices"
    )


def counter_cost_replacement_progress_matches(batch, effects, selections, requested_amount):
    """Rebuild a suspended fixed counter payment from its requested amount."""
    from .model import COUNTER_REPLACEMENT_GENERATED_FIELD

    event, = batch.events
    payload = {**event.payload, "amount": requested_amount}
    payload.pop("replacement_choosers", None)
    if COUNTER_REPLACEMENT_GENERATED_FIELD in payload:
        payload[COUNTER_REPLACEMENT_GENERATED_FIELD] = False
    original = replace(event, payload=payload, applied_effects=())
    progress = advance_replacement_batch(
        replace(batch, events=(original,), journal=()), effects, selections=tuple(selections),
    )
    return progress.pending is not None and progress.batch == batch


def _entry_object_choice_payload(choices, effects_by_id):
    object_choices = {}
    labels = set()
    for choice in choices:
        for effect_id in choice.options:
            operations = tuple(op for op in effects_by_id[effect_id].operations
                if isinstance(op, ENTRY_OBJECT_SELECTION_OPERATIONS))
            if not operations:
                continue
            if len(operations) != 1:
                raise ReplacementEffectError("One replacement choice requires one object selector")
            operation = operations[0]
            if isinstance(operation, GraveyardCopyEntryOperation):
                refs = [card.ref for card in operation.eligible_cards(choice.event)]
                object_choices[effect_id] = {"legal_refs":refs,"minimum":2,"maximum":2}
                labels.add("Choose the creature to copy first, then the creature whose power supplies counters")
                continue
            if isinstance(operation, LinkedGraveyardEntryOperation):
                refs = [card.ref for card in operation.eligible_cards(choice.event)]
                object_choices[effect_id] = {"legal_refs": refs, "minimum": 1,
                                             "maximum": min(operation.maximum, len(refs))}
                labels.add("Choose creature cards from your graveyard")
                continue
            if isinstance(operation, AmplifyEntryOperation):
                refs = [card.ref for card in operation.eligible_cards(choice.event)]
                labels.add("Cards to reveal for Amplify")
            elif isinstance(operation, ChosenTransferEntryCounters):
                refs = list(operation.eligible_refs(choice.event))
                labels.add("Permanents to remove counters from")
            else:
                scope = choice.event.entry_scope
                eligible = set(scope.eligible_zone_choices(card.object_id for card in operation.candidates)) if scope else set()
                refs = [card.ref for card in operation.candidates if card.object_id in eligible]
                labels.add("Permanents to sacrifice with Devour")
            object_choices[effect_id] = {"legal_refs": refs, "minimum": 0, "maximum": len(refs)}
    if not object_choices:
        return {}, {}
    refs = list(dict.fromkeys(ref for spec in object_choices.values() for ref in spec["legal_refs"]))
    return object_choices, {"type": "object_ref_array", "legal_refs": refs, "minimum": 0,
        "maximum": len(refs), "distinct": True, "required": False,
        "label": next(iter(labels)) if len(labels) == 1 else "Objects for the selected replacement"}


def replacement_choice_payload(
    pending: ReplacementBatchChoice,
    effects: Iterable[ReplacementEffect],
) -> dict[str, object]:
    by_id = {
        effect.effect_id: effect for effect in canonical_effects(effects)
    }
    options: list[dict[str, object]] = []
    choices=tuple(choice for _,choice in pending.event_choices) or (pending.choice,)
    for value in dict.fromkeys(value for choice in choices for value in choice.legal_selections):
        declined = value.startswith("decline:")
        effect_id = value.removeprefix("decline:") if declined else value
        effect = by_id[effect_id]
        label = effect.label or effect.effect_id
        options.append(
            {
                "id": value,
                "label": (effect.decline_label or f"Decline {label}") if declined else label,
                "source": effect.source_id,
                "decline": declined,
            }
        )
    legal_values = [str(option["id"]) for option in options]
    allocation_by_effect = {
        choice.effect_id: {
            "shield_id": choice.shield_id,
            "available": choice.available,
            "required": choice.allocation_required,
            "events": [
                {
                    "event_id": event_id,
                    "amount": amount,
                    "unpreventable": unpreventable,
                }
                for event_id, amount, unpreventable in choice.events
            ],
        }
        for choice in pending.prevention_allocations
    }
    allocation_event_ids = sorted(
        {
            event_id
            for choice in pending.prevention_allocations
            for event_id, _amount, _unpreventable in choice.events
        }
    )
    maximum_allocation = max(
        (
            amount
            for choice in pending.prevention_allocations
            for _event_id, amount, _unpreventable in choice.events
        ),
        default=0,
    )
    choice_schema: dict[str, object] = {
        "replacement": {"legal_values": legal_values}
    }
    if pending.event_order_options:
        choice_schema["replacement_event"] = {
            "legal_values": list(pending.event_order_options),
            "label": "Choose the next simultaneous event",
        }
    if allocation_event_ids:
        choice_schema["prevention_allocation"] = {
            "shape": "object_map",
            "legal_refs": allocation_event_ids,
            "legal_values": list(range(maximum_allocation + 1)),
            "required": False,
            "label": "Divide prevention (only when required)",
        }
    name_choices = {
        value: {"nonland": operation.nonland}
        for value in legal_values if (operation := entry_card_name_operation(by_id[value.removeprefix("decline:")]))
    }
    if name_choices:
        choice_schema["card_name"] = {"value_type": "card_name", "required": False, "nonempty": True}
    object_choices, object_schema = _entry_object_choice_payload(choices, by_id)
    if object_choices:
        choice_schema["replacement_objects"] = object_schema
    members = pending.tree_choice.counter_instruction_members
    placements = [
        {"kind": member.event.payload["counter_name"], "amount": member.event.payload["amount"]}
        for _, member in members
    ]
    return {
        "chooser": pending.choice.chooser,
        **({"counter_instruction_placements": placements} if placements else {}),
        "prompt": "Choose the next replacement or prevention effect.",
        "options": options,
        **({"card_name_choices": name_choices} if name_choices else {}),
        **({'object_choices':object_choices} if object_choices else {}),
        **({'event_effect_options':{event_id:list(choice.legal_selections) for event_id,choice in pending.event_choices}} if pending.event_choices else {}),
        **(
            {"event_order_options": list(pending.event_order_options)}
            if pending.event_order_options
            else {}
        ),
        **(
            {"prevention_allocations": allocation_by_effect}
            if allocation_by_effect
            else {}
        ),
        "legal_actions": [
            {
                "id": "choose",
                "action": "choose",
                "label": "Choose replacement",
                "choice_schema": {
                    **choice_schema,
                },
            }
        ],
    }
