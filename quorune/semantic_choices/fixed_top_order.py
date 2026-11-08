"""Typed private top-order choice with exact current incarnation pinning."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from ..library_order_model import (
    FixedTopLibraryOrderSpec, TOP_ORDER_OPERATION, TOP_ORDER_CAPABILITY,
    FixedHandTopOrderSpec, HAND_TOP_ORDER_OPERATION, HAND_TOP_ORDER_CAPABILITY,
    HAND_TOP_ORDER_RULES,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_partition import partition_refs, LibraryPartitionError
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .ordering import LibraryOrderingHandler
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)


class FixedTopLibraryOrderingHandler(LibraryOrderingHandler):
    def prepare(self, effect, context):
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


FIXED_TOP_ORDER_HANDLERS = (FixedTopLibraryOrderingHandler(
    operation=TOP_ORDER_OPERATION,
    handler_id='choice.ordering.fixed-controller-top.v1',
    capability_dependencies=(TOP_ORDER_CAPABILITY,),
    continuation_fields=(
        'count', 'player', '_choice_actor', '_looked_refs', '_stack_label',
        '_looked_incarnations',
    ),
    test_modules=('tests.test_fixed_top_order',),
), FixedHandTopOrderingHandler())
