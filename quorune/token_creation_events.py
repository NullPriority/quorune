from __future__ import annotations

"""Publish simultaneous token creation after the canonical object commit."""

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, TYPE_CHECKING

from .counter_placement_events import dispatch_prepared_counter_events
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
    entry_occurrences = []
    for object_id in creation.objects:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
        types, _subtypes, _supertypes = host._type_parts(str(data.get("type_line") or ""))
        entry_occurrences.append((card.logical_object_id, tuple(sorted(types))))
    for identity, types in entry_occurrences:
        host._record_turn_history(
            "permanent_entered", actor=controller, object_incarnation=identity, types=types,
        )
    return previous_quantity


def _entry_contexts(host: TokenCreationHost, creation: CommittedTokenCreation, reason: str) -> tuple[dict[str, Any], ...]:
    contexts = []
    for object_id in creation.objects:
        card = host.state.cards[object_id]
        data = host._effective_card_data(card)
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
    trigger_batch: list[Any] = []
    if prepared_counters is not None:
        dispatch_prepared_counter_events(host, prepared_counters, reason=reason, trigger_batch=trigger_batch)
    for creation, entries, previous_quantity in zip(creations, contexts, previous_quantities, strict=True):
        for context in entries:
            host._dispatch_semantic_event("token.created", context, trigger_batch=trigger_batch)
            host._dispatch_semantic_event("permanent.enter", context, trigger_batch=trigger_batch)
            for card_type in ("artifact", "creature", "land", "enchantment"):
                if card_type in context["types"]:
                    host._dispatch_semantic_event(f"{card_type}.enter", context, trigger_batch=trigger_batch)
        if entries:
            fact = TokenCreationBatchFact(
                creation.controller, len(entries),
                sum("creature" in context["types"] for context in entries), previous_quantity,
            )
            host._dispatch_semantic_event(TOKEN_CREATION_BATCH_EVENT, fact.to_context(), trigger_batch=trigger_batch)
    enqueue_trigger_batch(host, trigger_batch)
