"""Prepare and resume the physical departures of one collected APNAP choice."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..composition_batch_order_model import CompositionDepartureBatchRequest
from ..composition_departure_batch import PreparedCompositionDepartureBatch, prepare_composition_departure_batch
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement_batch
from ..zone_transitions import ZoneTransitionOwner
from .apnap_commit import APNAP_OBJECT_COMMIT_HANDLER_ID, APNAP_OBJECT_COMMIT_OPERATION
from .intent_replacement import (
    semantic_intent_identity, serialized_replacement_selections, validate_semantic_intent_identity,
)
from .model import SemanticChoiceContinuation, SemanticChoiceError
from .physical_ordering import physical_order_payload, physical_order_response


APNAP_PHYSICAL_ORDER_FIELD = "apnap_composition_order"


@dataclass(frozen=True, slots=True)
class ApnapCompositionOrder:
    continuation: SemanticChoiceContinuation
    request: CompositionDepartureBatchRequest
    intent_kind: str
    intent: FrozenMap
    replacement_selections: tuple
    orders: tuple[tuple[str, ...], ...] = ()

    def __post_init__(self):
        if (
            not isinstance(self.continuation, SemanticChoiceContinuation)
            or self.continuation.handler_id != APNAP_OBJECT_COMMIT_HANDLER_ID
            or self.continuation.effect.get("op") != APNAP_OBJECT_COMMIT_OPERATION
            or self.continuation.effect.get("actor") != self.continuation.semantic_frame.controller
            or not isinstance(self.request, CompositionDepartureBatchRequest)
            or not isinstance(self.replacement_selections, tuple)
            or any(not isinstance(value, (str, Mapping)) for value in self.replacement_selections)
            or not isinstance(self.orders, tuple) or any(not isinstance(value, tuple) for value in self.orders)
        ):
            raise ValueError("APNAP physical ordering needs its exact choice commit and ordered batch")
        object.__setattr__(self, "intent", FrozenMap(validate_semantic_intent_identity(self.intent_kind, self.intent)))
        object.__setattr__(self, "replacement_selections", tuple(
            FrozenMap(value) if isinstance(value, Mapping) else value for value in self.replacement_selections
        ))
        # Complete the uncollected groups with their offered order for validation only.
        self.request.ordered_ids((*self.orders, *(refs for _, refs in self.request.choices[len(self.orders):])))

    @property
    def current_choice(self):
        choices = self.request.choices
        return choices[len(self.orders)] if len(self.orders) < len(choices) else None

    def with_order(self, refs):
        if self.current_choice is None or not isinstance(refs, (list, tuple)):
            raise ValueError("No pending APNAP physical order accepts this response")
        return replace(self, orders=(*self.orders, tuple(refs)))

    def to_dict(self):
        return {
            "schema_version": 1, "continuation": self.continuation.to_dict(),
            "request": self.request.to_dict(), "intent_kind": self.intent_kind, "intent": thaw_value(self.intent),
            "replacement_selections": serialized_replacement_selections(self.replacement_selections),
            "orders": [list(row) for row in self.orders],
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "continuation", "request", "intent_kind", "intent", "replacement_selections", "orders"}
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or any(not isinstance(value[key], (tuple, list)) for key in ("orders", "replacement_selections"))
            or any(not isinstance(row, (tuple, list)) for row in value["orders"])
        ):
            raise ValueError("APNAP physical order continuation has a closed shape")
        from .defaults import default_semantic_choice_registry

        _handler, continuation = default_semantic_choice_registry().decode_continuation(value["continuation"])
        return cls(
            continuation, CompositionDepartureBatchRequest.from_dict(value["request"]), value["intent_kind"],
            value["intent"], tuple(value["replacement_selections"]), tuple(tuple(row) for row in value["orders"]),
        )


def _resolving_item(host, continuation):
    item = next((entry for entry in host.state.stack if entry.ref == continuation.stack_ref), None)
    actor = continuation.effect.get("actor")
    if (
        item is None or actor not in host.active_seats or item.controller != actor
        or item.context.get("currently_resolving") is not True or continuation.semantic_frame.controller != actor
    ):
        raise SemanticChoiceError("APNAP physical departure lost its original resolving controller")
    host._validate_semantic_frame(continuation.semantic_frame.to_dict(), item)
    program = host.semantics.get(item.semantic_key)
    if program is None or not host.semantic_program_is_current_trusted(program):
        raise SemanticChoiceError("APNAP physical departure lost its trusted parent instruction")
    return item


def _issue(host, frame):
    choice = frame.current_choice
    if choice is None:
        raise SemanticChoiceError("No APNAP physical ordering remains")
    (actor, _destination, _position), _refs = choice
    host.permissions.issue(
        kind="permanent.composition_order", role="pilot", actors=[actor], allowed_actions=["order"],
        payload_by_actor={actor: physical_order_payload(frame)},
        continuation={APNAP_PHYSICAL_ORDER_FIELD: frame.to_dict()},
    )


def prepare_apnap_composition_movement(
    host, continuation, intent, saved: ApnapCompositionOrder | None,
) -> tuple[PreparedCompositionDepartureBatch | None, bool]:
    """Resolve the whole movement before any physical card order is offered."""
    if not isinstance(intent, MoveObjectsSimultaneouslyIntent) or intent.expected_zones != ("battlefield",):
        if saved is not None:
            raise SemanticChoiceError("APNAP physical movement disappeared before completion")
        return None, False
    cards = tuple(host._resolve_object(intent.actor, ref, zones={"battlefield"}) for ref in intent.object_refs)
    if not any(card.permanent_composition is not None for card in cards):
        if saved is not None:
            raise SemanticChoiceError("APNAP physical movement no longer contains a merged permanent")
        return None, False
    item = _resolving_item(host, continuation)
    if item.controller != intent.actor:
        raise SemanticChoiceError("APNAP physical movement changed its resolving actor")
    changes = tuple((card.object_id, intent.destination) for card in cards)
    kinds = {card.object_id: intent.transition_kind for card in cards}
    replacement_plans = prepare_zone_change_replacement_batch(
        host, changes, transition_kinds=kinds, selections=intent.replacement_selections, error_type=GameRuleError,
    )
    batch = prepare_composition_departure_batch(
        ZoneTransitionOwner(host), changes, actor=item.controller, reason=intent.reason,
        transition_kinds=kinds, prepared_replacements=replacement_plans,
    )
    if saved is not None:
        if saved.continuation != continuation or saved.request != batch.request or saved.current_choice is not None:
            raise SemanticChoiceError("The APNAP physical batch changed before the submitted order could commit")
        return batch.with_orders(saved.orders), False
    if not batch.request.choices:
        return batch.with_orders(()), False
    kind, identity = semantic_intent_identity(intent)
    _issue(host, ApnapCompositionOrder(
        continuation, batch.request, kind, FrozenMap(identity), intent.replacement_selections,
    ))
    return None, True


def complete_apnap_composition_order(host, decision):
    from .defaults import default_semantic_choice_registry
    from .preparation_coordination import continue_semantic_preparation

    if set(decision.continuation) != {APNAP_PHYSICAL_ORDER_FIELD}:
        raise GameRuleError("APNAP physical-order continuation fields changed")
    try:
        saved = ApnapCompositionOrder.from_dict(decision.continuation[APNAP_PHYSICAL_ORDER_FIELD])
        item = _resolving_item(host, saved.continuation)
        if saved.request.apnap_order != tuple(host.apnap_order()):
            raise SemanticChoiceError("APNAP physical-order participants changed")
        ordered = saved.with_order(physical_order_response(host, decision, saved))
        if ordered.current_choice is not None:
            _issue(host, ordered)
            return
        handler, continuation = default_semantic_choice_registry().decode_continuation(saved.continuation.to_dict())
        preparation = handler.prepare(continuation.effect, host._semantic_choice_context(item, item.controller, continuation.effect))
        continue_semantic_preparation(
            host, continuation=continuation, actor=item.controller, preparation=preparation,
            expected_intent_kind=saved.intent_kind, expected_intent=thaw_value(saved.intent),
            replacement_selections=saved.replacement_selections, apnap_order=ordered,
        )
    except (TypeError, ValueError, KeyError, SemanticChoiceError) as exc:
        raise GameRuleError(str(exc)) from exc
