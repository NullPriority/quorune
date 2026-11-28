from __future__ import annotations

"""Publish simultaneous token creation after the canonical object commit."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from .counter_placement_events import dispatch_prepared_counter_events
from .permanent_event_state_model import BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD
from .replacement_effects import ReplacementChoiceRequired
from .token_creation_batch_model import TOKEN_CREATION_BATCH_EVENT, TokenCreationBatchFact
from .token_creation_identity import token_copy_snapshot
from .trigger_processing import enqueue_trigger_batch
from .zone_trigger_events import sealed_public_characteristic_facts

if TYPE_CHECKING:
    from .counter_placement import PreparedCounterPlacements
    from .token_creation import TokenCreationHost


def public_replacement_labels(batch, effects) -> tuple[tuple[str, str], ...]:
    """Describe prior public choices without projecting internal effect IDs."""
    by_id = {effect.effect_id: effect for effect in effects}
    labels = []
    for selection in batch.journal:
        declined = selection.effect_id.startswith("decline:")
        effect = by_id[selection.effect_id.removeprefix("decline:")]
        label = effect.label or f"replacement from {effect.source_id}"
        if declined:
            label = effect.decline_label or f"declined {label}"
        labels.append((selection.chooser, label))
    return tuple(labels)


class DistributedTokenChoiceRequired(ReplacementChoiceRequired):
    def __init__(self, *, batch, effects, pending, earlier=()):
        super().__init__(batch=batch, effects=effects, pending=pending)
        self.public_history = (*earlier, *public_replacement_labels(batch, self.effects))


@dataclass(frozen=True, slots=True)
class CommittedTokenCreation:
    controller: str
    objects: tuple[str, ...]
    base_name: str
    base_quantity: int
    replacement_components: tuple[Mapping[str, Any], ...]
    replacement_journal: tuple[Any, ...]


def _record_creation(host: TokenCreationHost, creation: CommittedTokenCreation, reason: str) -> int:
    controller = creation.controller
    tracker = host.state.players[controller].stats.setdefault("tokens_created_by_turn", {})
    turn_key = str(host.state.turn_sequence)
    previous_quantity = int(tracker.get(turn_key, 0))
    tracker[turn_key] = previous_quantity + len(creation.objects)
    host._log(
        controller,
        "token.create",
        f"{controller} created {len(creation.objects)} token(s).",
        {
            "objects": [host.state.cards[object_id].ref for object_id in creation.objects],
            "base_name": creation.base_name,
            "base_quantity": creation.base_quantity,
            "replacement_count": len(creation.objects) - creation.base_quantity,
            "replacement_components": [dict(value) for value in creation.replacement_components],
            "replacement_order": [selection.effect_id for selection in creation.replacement_journal],
            "reason": reason,
        },
        importance=1,
        changed_objects=creation.objects,
        changed_players=[controller],
    )
    from .turn_history import record_food_entry_history

    entry_occurrences = []
    for object_id in creation.objects:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
        types, subtypes, _supertypes = host._type_parts(str(data.get("type_line") or ""))
        entry_occurrences.append((card.logical_object_id, tuple(sorted(types)), tuple(sorted(subtypes))))
    history = getattr(host.state, "turn_history", None)
    entry_token = True if getattr(history, "permanent_entry_token_history_version", None) == 1 else None
    for identity, types, subtypes in entry_occurrences:
        host._record_turn_history(
            "permanent_entered", actor=controller, object_incarnation=identity, types=types, token=entry_token,
        )
        record_food_entry_history(host, controller=controller, incarnation=identity, types=types, subtypes=subtypes)
    return previous_quantity


def _entry_contexts(host: TokenCreationHost, creation: CommittedTokenCreation, reason: str) -> tuple[dict[str, Any], ...]:
    contexts = []
    for object_id in creation.objects:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
        from .tribute_entry_results import tribute_entry_identity_context
        from .devour import devour_entry_context
        from .rules.entry_cast_information import direct_token_entry_cast_context

        contexts.append({
            "card": card.ref,
            "card_object_identity": card.logical_object_id,
            "card_zone_change_counter": card.zone_change_counter,
            "copiable_snapshot": token_copy_snapshot(host, card),
            "controller": creation.controller,
            "owner": creation.controller,
            "from": "outside",
            "to": "battlefield",
            **sealed_public_characteristic_facts(data),
            "mana_value": float(data.get("mana_value", 0) or 0),
            "token": True,
            "tapped": card.tapped,
            "reason": reason,
            **tribute_entry_identity_context(card,card.logical_object_id),
            **devour_entry_context(card, card.logical_object_id),
            **direct_token_entry_cast_context(host, card),
        })
    return tuple(contexts)


def publish_token_creations(
    host: TokenCreationHost,
    creations: Sequence[CommittedTokenCreation],
    *,
    reason: str,
    prepared_counters: PreparedCounterPlacements | None = None,
) -> None:
    """Seal every creator's history and entry facts before dispatch."""
    previous_quantities = tuple(_record_creation(host, creation, reason) for creation in creations)
    contexts = tuple(_entry_contexts(host, creation, reason) for creation in creations)
    from .rules.unpaid_creature_entry import GROUP_FIELD, token_members

    unpaid_members = token_members(host, contexts)
    trigger_batch: list[Any] = []
    if prepared_counters is not None:
        dispatch_prepared_counter_events(host, prepared_counters, reason=reason, trigger_batch=trigger_batch)
    for creation, entries, previous_quantity in zip(creations, contexts, previous_quantities, strict=True):
        for context in entries:
            host._dispatch_semantic_event("token.created", context, trigger_batch=trigger_batch)
            entry = context
            if getattr(host.state, BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD, None) == 1:
                entry = {**context, "putting_player": creation.controller}
            permanent_entry = {**entry, GROUP_FIELD: unpaid_members} if unpaid_members else entry
            host._dispatch_semantic_event("permanent.enter", permanent_entry, trigger_batch=trigger_batch)
            for card_type in ("artifact", "creature", "land", "enchantment"):
                if card_type in entry["types"]:
                    host._dispatch_semantic_event(f"{card_type}.enter", entry, trigger_batch=trigger_batch)
        if entries:
            fact = TokenCreationBatchFact(
                creation.controller, len(entries),
                sum("creature" in context["types"] for context in entries), previous_quantity,
            )
            host._dispatch_semantic_event(TOKEN_CREATION_BATCH_EVENT, fact.to_context(), trigger_batch=trigger_batch)
    enqueue_trigger_batch(host, trigger_batch)
