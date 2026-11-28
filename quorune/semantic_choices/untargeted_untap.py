"""Choose current lands at resolution and delegate one simultaneous untap."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import SetPermanentsTappedIntent, RecordChoiceIntent
from ..untargeted_untap_model import (
    FixedLandUntapChoiceSpec, UNTARGETED_UNTAP_OPERATION, UNTARGETED_UNTAP_CAPABILITY,
)
from .model import (
    AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_LOCAL_FIELDS = {'_choice_actor', '_legal_objects', '_stack_label'}
_RESPONSE_FIELDS = {
    'objects', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema',
}


def _eligible(row, spec: FixedLandUntapChoiceSpec, actor: str, allowed) -> bool:
    return bool(
        row is not None and row.zone == 'battlefield' and not row.phased_out
        and 'land' in row.types
        and (spec.controller_scope == 'any' or row.controller == actor)
        and (not spec.scheduled or row.tapped and row.ref in allowed)
    )


def _current_selected_land(row, spec, actor, logical_object_id, allowed):
    return _eligible(row, spec, actor, allowed) and row.logical_object_id == logical_object_id


@dataclass(frozen=True, slots=True)
class FixedLandUntapChoiceHandler:
    operation: str = UNTARGETED_UNTAP_OPERATION
    handler_id: str = 'choice.public.fixed-land-untap.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 115.1a', 'CR 608.2d', 'CR 608.2f', 'CR 701.26b')
    capability_dependencies: tuple[str, ...] = (UNTARGETED_UNTAP_CAPABILITY, 'permanent.untap.effect')
    continuation_fields: tuple[str, ...] = (
        'player', 'maximum', 'controller_scope', 'schema_version', '_choice_actor', '_legal_objects', '_stack_label',
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'objects', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('SetPermanentsTappedIntent', 'tap_state.set_permanent_tapped')
    replay_fixture: str = 'fixed-untargeted-land-untap'
    test_modules: tuple[str, ...] = ('tests.test_untargeted_untap', 'tests.test_untargeted_untap_native')

    def prepare(self, effect, context):
        try:
            spec = FixedLandUntapChoiceSpec.from_effect(effect)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if effect['player'] != context.actor or not spec.scheduled and context.actor != context.stack_controller:
            raise SemanticChoiceError("Land choice requires its resolving controller or sealed participant")
        allowed = frozenset(context.query.choice_candidate_refs()) if spec.scheduled else ()
        rows = tuple(sorted(
            (row for row in context.query.objects(zones=('battlefield',)) if _eligible(row, spec, context.actor, allowed)),
            key=lambda row: row.ref,
        ))
        if not rows:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='no eligible lands'),
            )
        if any(not row.logical_object_id for row in rows):
            raise SemanticChoiceError('A land lacks a current incarnation')
        refs = tuple(row.ref for row in rows)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Untap a land you control." if spec.scheduled else f"Untap up to {spec.maximum} lands.",
                choice=ObjectChoice(
                    field_name="objects", legal_refs=refs, zones=("battlefield",),
                    minimum=1 if spec.scheduled else 0, maximum=min(spec.maximum, len(refs)), optional=not spec.scheduled,
                ),
                public_context=FrozenMap({'stack': context.stack_ref, 'operation': self.operation, 'objects': [{'id': row.ref, 'name': row.printed_name} for row in rows]}),
            ),
            continuation_effect=FrozenMap({
                **dict(effect), '_choice_actor': context.actor, '_stack_label': context.stack_label,
                '_legal_objects': {row.ref: row.logical_object_id for row in rows},
            }),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        try:
            spec = FixedLandUntapChoiceSpec.from_effect({k: v for k, v in effect.items() if k not in _LOCAL_FIELDS})
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if set(effect) != set(spec.effect(effect["player"])) | _LOCAL_FIELDS or effect["player"] != effect["_choice_actor"]:
            raise SemanticChoiceError('Land choice continuation fields or actor changed')
        raw = response.get('objects')
        if (
            set(response) - _RESPONSE_FIELDS or not isinstance(raw, (list, tuple))
            or not (1 if spec.scheduled else 0) <= len(raw) <= spec.maximum
            or any(type(ref) is not str or not ref for ref in raw)
            or len(set(raw)) != len(raw)
        ):
            raise SemanticChoiceError('Choose a distinct bounded land set')
        legal = effect['_legal_objects']
        if not isinstance(legal, Mapping):
            raise SemanticChoiceError('Land choice has no pinned incarnations')
        actor = effect['_choice_actor']
        allowed = frozenset(query.choice_candidate_refs()) if spec.scheduled else ()
        for ref in raw:
            row = query.object(ref, zones=('battlefield',))
            if not _current_selected_land(row, spec, actor, legal.get(ref), allowed):
                raise SemanticChoiceError('A chosen land is stale or ineligible')
        selected = tuple(raw)
        intents = (SetPermanentsTappedIntent(object_refs=selected, actor=actor, tapped=False, reason=effect['_stack_label']),) if selected else ()
        return SemanticChoiceCompletion(intents=(*intents, RecordChoiceIntent(
            actor=actor, event_code='permanent.untap.choice', message=f'{actor} chose {len(selected)} lands to untap.',
            details=FrozenMap({'stack': continuation.stack_ref, 'objects': selected}),
        )))
