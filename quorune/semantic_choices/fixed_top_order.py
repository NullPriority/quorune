"""Typed private top-order choice with exact current incarnation pinning."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..library_order_model import (
    FixedTopLibraryOrderSpec, TOP_ORDER_OPERATION, TOP_ORDER_CAPABILITY,
    FixedHandTopOrderSpec, HAND_TOP_ORDER_OPERATION, HAND_TOP_ORDER_CAPABILITY,
    HAND_TOP_ORDER_RULES,
    GRAVEYARD_LIBRARY_ORDER_OPERATION, GRAVEYARD_LIBRARY_ORDER_CAPABILITY,
    GRAVEYARD_LIBRARY_ORDER_RULES, DepartedLibraryArrivalOrder, LibraryArrivalOrder,
    PAID_PRIVATE_TOP_CYCLE, paid_private_top_cycle_effect, uses_paid_private_top_cycle,
    validate_graveyard_library_order_effect, validate_paid_private_top_cycle,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_partition import partition_refs, LibraryPartitionError
from ..rules.library_selection import LibrarySelectionArrangement, LibrarySelectionObjectIdentity
from ..semantic_runtime.intents import (
    LibrarySelectionIntent, MoveObjectsSimultaneouslyIntent, PayLifeIntent, ReorderLibraryTopIntent,
    RevealLibraryCardsIntent, ShuffleLibraryIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .ordering import LibraryOrderingHandler
from .model import (
    AutoContinue, ObjectChoice, OrderingChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


_PAID_CYCLE_STATE = "_paid_private_top_cycle_state"


@dataclass(frozen=True, slots=True)
class PaidPrivateTopCycleState:
    actor: str
    stack_ref: str
    label: str
    looked: tuple[LibrarySelectionObjectIdentity, ...]
    stage: str = "decide"

    def __post_init__(self):
        if (any(type(value) is not str or not value for value in (self.actor, self.stack_ref, self.label))
                or self.stage not in {"decide", "bottom", "top"}
                or not isinstance(self.looked, tuple) or len(self.looked) > 5
                or any(not isinstance(card, LibrarySelectionObjectIdentity) for card in self.looked)
                or len(set(self.refs)) != len(self.looked)
                or len({card.object_id for card in self.looked}) != len(self.looked)
                or len({card.logical_object_id for card in self.looked}) != len(self.looked)
                or self.stage == "bottom" and len(self.looked) < 2):
            raise SemanticChoiceError("The paid private look lost its actor, stage, or exact cards")

    @property
    def refs(self):
        return tuple(card.ref for card in self.looked)

    def effect(self):
        return FrozenMap({
            **paid_private_top_cycle_effect(self.actor),
            _PAID_CYCLE_STATE: {
                "actor": self.actor, "stack_ref": self.stack_ref, "label": self.label, "stage": self.stage,
                "looked": [card.to_dict() for card in self.looked],
            },
        })

    @classmethod
    def from_effect(cls, effect):
        raw = effect.get(_PAID_CYCLE_STATE)
        if not isinstance(raw, Mapping) or set(raw) != {"actor", "stack_ref", "label", "stage", "looked"}:
            raise SemanticChoiceError("The private cycle continuation requires its original looked group")
        looked = tuple(LibrarySelectionObjectIdentity.from_dict(card) for card in raw["looked"])
        state = cls(raw["actor"], raw["stack_ref"], raw["label"], looked, raw["stage"])
        if FrozenMap(effect) != state.effect():
            raise SemanticChoiceError("The private cycle continuation changed its represented instruction")
        return state


def _paid_top_cycle_group(query, actor):
    rows = tuple(query.object(ref, zones=("library",)) for ref in query.library_refs(actor, top_first=True)[:5])
    if any(row is None or row.owner != actor or not row.physical_card or not row.logical_object_id for row in rows):
        raise SemanticChoiceError("The looked prefix requires exact physical cards in its owner's library")
    return rows


def _cycle_identities(rows):
    return tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)


def _bound_paid_top_cycle(state, query, controller, stack_ref):
    stack = query.stack_object(stack_ref)
    if (state.actor != controller or state.stack_ref != stack_ref or state.actor not in query.active_seats
            or stack is None or stack.controller != state.actor or stack.label != state.label or stack.targets):
        raise SemanticChoiceError("The private cycle has left its original resolving state")
    rows = _paid_top_cycle_group(query, state.actor)
    if _cycle_identities(rows) != state.looked:
        raise SemanticChoiceError("The exact looked prefix changed before the private choice")
    return rows


def _paid_top_cycle_request(state, rows):
    if state.stage == "decide":
        choice = ScalarChoice("choice", ("stop", "pay"))
        prompt = "Stop and shuffle the rest, or pay 1 life to look again?"
    else:
        bottom = state.stage == "bottom"
        choice = OrderingChoice(
            field_name="cards", legal_refs=state.refs, visibility="actor_private",
            schema_extras=FrozenMap({"order": "bottom_to_top" if bottom else "top_to_bottom"}),
        )
        prompt = ("Order these cards from the library bottom upward." if bottom
                  else "Order the last looked-at cards from the library top downward.")
    return SemanticChoiceRequest(prompt, choice, FrozenMap({
        "stack": state.stack_ref, "operation": TOP_ORDER_OPERATION, "stage": state.stage,
        "cards": [{"id": row.ref, "name": row.printed_name} for row in rows],
    }))


def _paid_top_cycle_bottom(state, order):
    return LibrarySelectionIntent(
        actor=state.actor, player=state.actor, source_stack_ref=state.stack_ref, reason=state.label,
        arrangement=LibrarySelectionArrangement(
            looked=state.looked, selected_refs=(), remainder_refs=order,
            remainder_destination="library_bottom", remainder_order="chosen",
        ),
        looked_are_public=False, selected_are_public=False,
    )


def _paid_top_cycle_finish(state, order):
    if not order:
        return ShuffleLibraryIntent(state.actor, state.actor, state.label)
    return ReorderLibraryTopIntent(state.actor, state.actor, state.actor, order, state.label, shuffle_remainder=True)


def _prepare_paid_top_cycle(effect, context):
    actor = context.actor
    if actor != context.stack_controller:
        raise SemanticChoiceError("The resolving controller owns the private library look")
    inspected = ()
    if _PAID_CYCLE_STATE in effect:
        state = PaidPrivateTopCycleState.from_effect(effect)
        rows = _bound_paid_top_cycle(state, context.query, context.stack_controller, context.stack_ref)
    else:
        validate_paid_private_top_cycle(effect, actor=actor)
        rows = _paid_top_cycle_group(context.query, actor)
        state = PaidPrivateTopCycleState(actor, context.stack_ref, context.stack_label, _cycle_identities(rows))
        _bound_paid_top_cycle(state, context.query, context.stack_controller, context.stack_ref)
        if state.looked:
            inspected = (RevealLibraryCardsIntent(actor, actor, actor, state.refs, state.label),)
    if state.stage == "decide" and context.query.player_life_for_payment(actor) < 1:
        state = replace(state, stage="top")
    if state.stage == "top" and len(state.refs) < 2:
        return SemanticChoicePreparation(
            None, state.effect(), preparation_intents=(*inspected, _paid_top_cycle_finish(state, state.refs)),
            auto_continue=AutoContinue(reason="Shuffle and restore the final private look"),
        )
    return SemanticChoicePreparation(
        _paid_top_cycle_request(state, rows), state.effect(), preparation_intents=inspected,
    )


def _complete_paid_top_cycle(continuation, response, query):
    state = PaidPrivateTopCycleState.from_effect(continuation.effect)
    rows = _bound_paid_top_cycle(state, query, continuation.semantic_frame.controller, continuation.stack_ref)
    field = "choice" if state.stage == "decide" else "cards"
    if (set(response) - {"action", "action_id", "choice_schema"} != {field}
            or response.get("action", "choose") != "choose" or response.get("action_id", "choose") != "choose"):
        raise SemanticChoiceError("Provide only the currently authorized private cycle response")
    issued = _paid_top_cycle_request(state, rows).choice.choice_schema()
    if "choice_schema" in response and FrozenMap(response["choice_schema"]) != FrozenMap(issued):
        raise SemanticChoiceError("The private cycle response changed its issued choice")
    value = response[field]
    restart = FrozenMap(paid_private_top_cycle_effect(state.actor))
    if state.stage == "decide":
        if value == "stop":
            return SemanticChoiceCompletion(repeat_effect=replace(state, stage="top").effect())
        if value != "pay" or query.player_life_for_payment(state.actor) < 1:
            raise SemanticChoiceError("The resolving controller cannot pay for another look")
        payment = PayLifeIntent(state.actor, state.actor, 1, state.label)
        if len(state.refs) >= 2:
            return SemanticChoiceCompletion(intents=(payment,), repeat_effect=replace(state, stage="bottom").effect())
        bottom = (_paid_top_cycle_bottom(state, state.refs),) if state.refs else ()
        return SemanticChoiceCompletion(intents=(payment, *bottom), repeat_effect=restart)
    order = partition_refs(value, field="cards")
    if len(order) != len(state.refs) or set(order) != set(state.refs):
        raise SemanticChoiceError("Order every card in the exact looked group once")
    if state.stage == "bottom":
        return SemanticChoiceCompletion(intents=(_paid_top_cycle_bottom(state, order),), repeat_effect=restart)
    return SemanticChoiceCompletion(intents=(_paid_top_cycle_finish(state, order),))


class FixedTopLibraryOrderingHandler(LibraryOrderingHandler):
    def prepare(self, effect, context):
        if uses_paid_private_top_cycle(effect):
            try:
                return _prepare_paid_top_cycle(effect, context)
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
        try:
            spec = FixedTopLibraryOrderSpec.from_effect(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if effect['player'] != context.actor or context.actor != context.stack_controller:
            raise SemanticChoiceError('Top order requires its resolving controller')
        prepared = super().prepare(effect, context)
        if prepared.request is None:
            return prepared
        refs = tuple(context.query.library_refs(context.actor, top_first=True)[:spec.count])
        identities = {}
        for ref in refs:
            row = context.query.object(ref, zones=('library',))
            if row is None or not row.logical_object_id:
                raise SemanticChoiceError('Looked card lost its current incarnation')
            identities[ref] = row.logical_object_id
        return replace(prepared, continuation_effect=FrozenMap({
            **dict(prepared.continuation_effect), '_looked_incarnations': identities,
        }))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if uses_paid_private_top_cycle(effect):
            try:
                return _complete_paid_top_cycle(continuation, response, query)
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
        local = {'_choice_actor', '_looked_refs', '_stack_label', '_looked_incarnations'}
        try:
            spec = FixedTopLibraryOrderSpec.from_effect({
                k: v for k, v in effect.items() if k not in local
            })
            expected = partition_refs(effect.get('_looked_refs'), field='looked_refs')
            selected = partition_refs(response.get('cards'), field='cards')
        except (TypeError, ValueError, LibraryPartitionError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if set(effect) != {'op', 'player', 'count', *local} or effect['player'] != effect['_choice_actor']:
            raise SemanticChoiceError('Top order continuation fields or actor changed')
        actor = effect['_choice_actor']
        identities = effect['_looked_incarnations']
        if (
            tuple(query.library_refs(actor, top_first=True)[:spec.count]) != expected
            or set(identities) != set(expected)
        ):
            raise SemanticChoiceError('The pinned top set changed')
        for ref in expected:
            row = query.object(ref, zones=('library',))
            if row is None or row.logical_object_id != identities[ref]:
                raise SemanticChoiceError('A looked-at card changed incarnation')
        if len(selected) != len(expected) or set(selected) != set(expected):
            raise SemanticChoiceError('Order every looked-at card exactly once')
        return super().complete(continuation, {'cards': selected}, query)


_HAND_TOP_FIELDS = ("_choice_actor", "_hand_incarnations", "_stack_label")


def _private_hand_rows(query: SemanticChoiceQuery, actor: str):
    rows = tuple(sorted(query.objects(zones=("hand",), owner=actor), key=lambda row: row.ref))
    if any(not row.logical_object_id or not row.known_to_actor for row in rows):
        raise SemanticChoiceError("Hand order requires the actor's known current hand")
    return rows


@dataclass(frozen=True, slots=True)
class FixedHandTopOrderingHandler:
    operation: str = HAND_TOP_ORDER_OPERATION
    handler_id: str = "choice.ordering.fixed-hand-to-library-top.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = HAND_TOP_ORDER_RULES
    capability_dependencies: tuple[str, ...] = (HAND_TOP_ORDER_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("op", "player", "count", *_HAND_TOP_FIELDS)
    private_data: tuple[str, ...] = ("actor hand", "selected top order")
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("MoveObjectsSimultaneouslyIntent", "ZoneTransitionOwner.move_cards_simultaneously")
    replay_fixture: str = "fixed-private-hand-to-library-top"
    test_modules: tuple[str, ...] = ("tests.test_fixed_top_order", "tests.test_top_order_native")

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        try:
            spec = FixedHandTopOrderSpec.from_effect(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        actor = context.actor
        if effect["player"] != actor or actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("Hand order requires its active resolving controller")
        rows = _private_hand_rows(context.query, actor)
        required = min(spec.count, len(rows))
        if not required:
            return SemanticChoicePreparation(None, FrozenMap(effect), auto_continue=AutoContinue(reason="empty hand"))
        choice = ObjectChoice(
            field_name="cards", legal_refs=tuple(row.ref for row in rows), zones=("hand",),
            minimum=required, maximum=required, visibility="actor_private",
            schema_extras=FrozenMap({"order": "top_to_bottom"}),
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Put {required} card(s) from your hand on top of your library, top card first.",
                choice=choice,
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation,
                    "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]}),
            ),
            continuation_effect=FrozenMap({**dict(effect), "_choice_actor": actor,
                "_hand_incarnations": {row.ref: row.logical_object_id for row in rows},
                "_stack_label": context.stack_label}),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
                 query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        effect = continuation.effect
        try:
            spec = FixedHandTopOrderSpec.from_effect({key: value for key, value in effect.items() if key not in _HAND_TOP_FIELDS})
            refs = partition_refs(response.get("cards"), field="cards")
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if not set(_HAND_TOP_FIELDS) <= set(effect):
            raise SemanticChoiceError("Hand order continuation fields changed")
        actor = effect["_choice_actor"]
        reason = effect["_stack_label"]
        if effect["player"] != actor or actor not in query.active_seats or type(reason) is not str or not reason:
            raise SemanticChoiceError("Hand order continuation actor or reason changed")
        rows = _private_hand_rows(query, actor)
        identities = {row.ref: row.logical_object_id for row in rows}
        if identities != effect["_hand_incarnations"]:
            raise SemanticChoiceError("The pinned hand changed")
        if not refs or len(refs) != min(spec.count, len(rows)) or not set(refs) <= identities.keys():
            raise SemanticChoiceError("Choose the required distinct cards from your hand")
        # The zone owner appends each surviving card at the top in batch order.
        intent = MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=tuple(reversed(refs)), expected_zones=("hand",),
            destination="library", reason=reason, owned_only=True,
        )
        return SemanticChoiceCompletion(intents=(intent,))


def _targeted_graveyard_rows(effect, query, stack_ref, actor):
    try:
        validate_graveyard_library_order_effect(effect, resolved=True)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    parent = query.stack_object(stack_ref)
    refs = tuple(effect["cards"])
    if (parent is None or actor != parent.controller or actor not in query.active_seats
            or refs != tuple(ref for ref in parent.targets if ref is not None)):
        raise SemanticChoiceError("Library order requires the controller's surviving stack targets")
    rows = tuple(query.object(ref, zones=("graveyard",)) for ref in refs)
    if any(row is None or not row.logical_object_id or row.token for row in rows):
        raise SemanticChoiceError("Library order requires current graveyard card incarnations")
    owners = {row.owner for row in rows}
    if len(owners) > 1 or not owners.issubset(query.active_seats) or (
        owners and (actor in owners) != (effect["scope"] == "own")
    ):
        raise SemanticChoiceError("Library order targets must belong to the instructed graveyard")
    return parent, rows


def _graveyard_library_move(effect, parent, order):
    return MoveObjectsSimultaneouslyIntent(
        actor=parent.controller, object_refs=tuple(effect["cards"]), expected_zones=("graveyard",),
        destination="library", reason=parent.label, owned_only=effect["scope"] == "own",
        library_order=LibraryArrivalOrder(parent.controller, effect["position"], order),
    )


def _departed_library_order_rows(effect, query, stack_ref, actor):
    fields = {"op", "scope", "player", "cards", "position", "incarnations"}
    if (set(effect) != fields or effect["op"] != GRAVEYARD_LIBRARY_ORDER_OPERATION
            or effect["scope"] != "event_departure" or effect["player"] != actor
            or actor not in query.active_seats):
        raise SemanticChoiceError("The departed library order requires its original library owner")
    try:
        refs = partition_refs(effect["cards"], field="cards")
        order = DepartedLibraryArrivalOrder(actor, effect["position"], refs)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    parent = query.stack_object(stack_ref)
    rows = tuple(query.object(ref, zones=order.origin_zones) for ref in refs)
    if (parent is None or any(row is None or row.owner != actor or row.token for row in rows)
            or effect["incarnations"] != {row.ref: row.logical_object_id for row in rows}):
        raise SemanticChoiceError("A departed library card lost its original public incarnation")
    return parent, order, rows


def _prepare_departed_library_order(effect, context):
    parent, order, rows = _departed_library_order_rows(effect, context.query, context.stack_ref, context.actor)
    choice = OrderingChoice(
        "cards", order.members, visibility="actor_private",
        schema_extras=FrozenMap({"order": "bottom_to_top" if order.position == "bottom" else "top_to_bottom"}),
    )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose the order of the cards entering your library.",
            choice=choice,
            public_context=FrozenMap({"stack": parent.ref, "operation": GRAVEYARD_LIBRARY_ORDER_OPERATION,
                "objects": [{"id": row.ref, "name": row.printed_name if row.known_to_actor else "Face-down card"}
                            for row in rows]}),
        ),
        continuation_effect=FrozenMap(effect),
    )


def _complete_departed_library_order(continuation, response, query):
    effect = continuation.effect
    parent, order, _ = _departed_library_order_rows(effect, query, continuation.stack_ref, effect["player"])
    try:
        chosen = partition_refs(response.get("cards"), field="cards")
    except LibraryPartitionError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if set(chosen) != set(order.members) or parent.controller != continuation.semantic_frame.controller:
        raise SemanticChoiceError("Order each original departed card exactly once")
    return SemanticChoiceCompletion(intents=(MoveObjectsSimultaneouslyIntent(
        actor=parent.controller, object_refs=order.members, expected_zones=order.origin_zones,
        destination="library", reason=parent.label, library_order=replace(order, members=chosen),
    ),))


@dataclass(frozen=True, slots=True)
class GraveyardLibraryOrderingHandler:
    operation: str = GRAVEYARD_LIBRARY_ORDER_OPERATION
    handler_id: str = "choice.ordering.graveyard-to-library.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = GRAVEYARD_LIBRARY_ORDER_RULES
    capability_dependencies: tuple[str, ...] = (GRAVEYARD_LIBRARY_ORDER_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        "op", "cards", "position", "scope", "_graveyard_incarnations", "player", "incarnations",
    )
    private_data: tuple[str, ...] = ("selected library order",)
    projected_fields: tuple[str, ...] = ("prompt", "objects", "legal_actions.choice_schema")
    mutation_path: tuple[str, ...] = ("MoveObjectsSimultaneouslyIntent", "ZoneTransitionOwner.move_cards_simultaneously")
    replay_fixture: str = "targeted-private-graveyard-library-order"
    test_modules: tuple[str, ...] = ("tests.test_fixed_public_zone_moves",)

    def prepare(self, effect: Mapping[str, Any], context: SemanticChoiceContext) -> SemanticChoicePreparation:
        if effect.get("scope") == "event_departure":
            return _prepare_departed_library_order(effect, context)
        cards = effect.get("cards")
        if isinstance(cards, (list, tuple)) and None in cards:
            effect = {**effect, "cards": [ref for ref in cards if ref is not None]}
        parent, rows = _targeted_graveyard_rows(effect, context.query, context.stack_ref, context.actor)
        refs = tuple(row.ref for row in rows)
        if len(refs) < 2:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), auto_continue=AutoContinue(reason="no library permutation needed"),
                preparation_intents=(_graveyard_library_move(effect, parent, refs),) if refs else (),
            )
        position = effect["position"]
        choice = OrderingChoice(
            "cards", refs, visibility="actor_private",
            schema_extras=FrozenMap({"order": "top_to_bottom" if position == "top" else "bottom_to_top"}),
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Order the targeted cards at the {position} of their owner's library.",
                choice=choice,
                public_context=FrozenMap({"stack": parent.ref, "operation": self.operation,
                    "objects": [{"id": row.ref, "name": row.printed_name} for row in rows]}),
            ),
            continuation_effect=FrozenMap({
                **dict(effect), "_graveyard_incarnations": {row.ref: row.logical_object_id for row in rows},
            }),
        )

    def complete(self, continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
                 query: SemanticChoiceQuery) -> SemanticChoiceCompletion:
        if continuation.effect.get("scope") == "event_departure":
            return _complete_departed_library_order(continuation, response, query)
        effect = dict(continuation.effect)
        incarnations = effect.pop("_graveyard_incarnations", None)
        parent, rows = _targeted_graveyard_rows(
            effect, query, continuation.stack_ref, continuation.semantic_frame.controller,
        )
        if len(rows) < 2 or incarnations != {row.ref: row.logical_object_id for row in rows}:
            raise SemanticChoiceError("The pinned graveyard targets changed")
        try:
            order = partition_refs(response.get("cards"), field="cards")
        except LibraryPartitionError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if set(order) != set(incarnations):
            raise SemanticChoiceError("Order every surviving graveyard target exactly once")
        return SemanticChoiceCompletion(intents=(_graveyard_library_move(effect, parent, order),))


FIXED_TOP_ORDER_HANDLERS = (FixedTopLibraryOrderingHandler(
    operation=TOP_ORDER_OPERATION,
    handler_id='choice.ordering.fixed-controller-top.v1',
    capability_dependencies=(TOP_ORDER_CAPABILITY,),
    continuation_fields=(
        'count', 'player', '_choice_actor', '_looked_refs', '_stack_label',
        '_looked_incarnations', PAID_PRIVATE_TOP_CYCLE, _PAID_CYCLE_STATE,
    ),
    test_modules=('tests.test_fixed_top_order',),
), FixedHandTopOrderingHandler(), GraveyardLibraryOrderingHandler())
