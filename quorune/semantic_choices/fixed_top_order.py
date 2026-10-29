"""Typed private top-order choice with exact current incarnation pinning."""
from dataclasses import replace

from ..library_order_model import (
    FixedTopLibraryOrderSpec, TOP_ORDER_OPERATION, TOP_ORDER_CAPABILITY,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_partition import partition_refs, LibraryPartitionError
from .ordering import LibraryOrderingHandler
from .model import SemanticChoiceError


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


FIXED_TOP_ORDER_HANDLERS = (FixedTopLibraryOrderingHandler(
    operation=TOP_ORDER_OPERATION,
    handler_id='choice.ordering.fixed-controller-top.v1',
    capability_dependencies=(TOP_ORDER_CAPABILITY,),
    continuation_fields=(
        'count', 'player', '_choice_actor', '_looked_refs', '_stack_label',
        '_looked_incarnations',
    ),
    test_modules=('tests.test_fixed_top_order',),
),)
