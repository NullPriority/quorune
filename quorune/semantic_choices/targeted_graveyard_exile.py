"""One required public card choice by the affected graveyard owner."""
from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..rules.targeted_graveyard_exile import DEPENDENCIES
from ..targeted_graveyard_exile_model import GraveyardExileChoiceSpec, GRAVEYARD_EXILE_CHOICE_OPERATION
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
from .model import (AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest)


def _validate(effect, actor):
    try:
        return GraveyardExileChoiceSpec.from_effect(effect, player=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _candidates(query, actor):
    refs = query.choice_candidate_refs()
    rows = tuple(query.object(ref, zones=('graveyard',)) for ref in refs)
    if (len(refs) != len(set(refs)) or any(row is None or row.token or not row.known_to_actor
            or row.owner != actor or row.zone != 'graveyard' for row in rows)):
        raise SemanticChoiceError('Graveyard exile candidates require distinct current public owned cards')
    try:
        identities = tuple(LibrarySelectionObjectIdentity(row.object_id, row.logical_object_id, row.ref) for row in rows)
    except (ValueError, TypeError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if (any(len({getattr(value, key) for value in identities}) != len(identities)
                for key in ('object_id', 'logical_object_id', 'ref'))):
        raise SemanticChoiceError('Graveyard exile candidates require distinct current public owned cards')
    return tuple(sorted(zip(identities, rows), key=lambda pair: pair[0].ref))


@dataclass(frozen=True, slots=True)
class TargetedGraveyardExileChoiceHandler:
    operation: str = GRAVEYARD_EXILE_CHOICE_OPERATION
    handler_id: str = 'choice.graveyard.target-player-exile.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('115.10a', '400.2', '400.7', '608.2b', '608.2d', '701.13a')
    capability_dependencies: tuple[str, ...] = DEPENDENCIES
    continuation_fields: tuple[str, ...] = ('player', 'selection', '_choice_actor', '_stack_label', '_graveyard_candidates')
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ('prompt', 'objects', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('MoveObjectsSimultaneouslyIntent', 'ZoneTransitionOwner.move_cards_simultaneously')
    replay_fixture: str = 'target-player-graveyard-exile'
    test_modules: tuple[str, ...] = ('tests.test_targeted_graveyard_exile',)

    def prepare(self, effect, context):
        _validate(effect, context.actor)
        if context.actor not in context.query.seats:
            raise SemanticChoiceError('Graveyard exile chooser must be a known player')
        if context.actor not in context.query.active_seats:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='The affected graveyard owner left the game'))
        candidates = _candidates(context.query, context.actor)
        if not candidates:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='The affected graveyard is empty'))
        return SemanticChoicePreparation(request=SemanticChoiceRequest(
            prompt='Choose a card from your graveyard to exile.',
            choice=ObjectChoice(field_name='objects', legal_refs=tuple(identity.ref for identity, _ in candidates),
                zones=('graveyard',), minimum=1, maximum=1, owner_relation='actor'),
            public_context=FrozenMap({'stack': context.stack_ref, 'operation': self.operation,
                'objects': [{'id': identity.ref, 'name': row.printed_name} for identity, row in candidates]})),
            continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
                '_stack_label': context.stack_label,
                '_graveyard_candidates': tuple(identity.to_dict() for identity, _ in candidates)}))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if set(effect) != {'op', 'player', 'selection', '_choice_actor', '_stack_label', '_graveyard_candidates'}:
            raise SemanticChoiceError('Graveyard exile continuation fields are closed')
        actor = effect['_choice_actor']
        spec = _validate({key: effect[key] for key in ('op', 'player', 'selection')}, actor)
        if actor not in query.seats or actor not in query.active_seats:
            raise SemanticChoiceError('The affected graveyard owner is not active')
        label, raw = effect['_stack_label'], effect['_graveyard_candidates']
        if type(label) is not str or not label or not isinstance(raw, (list, tuple)):
            raise SemanticChoiceError('Graveyard exile continuation identities are missing')
        try:
            saved = tuple(LibrarySelectionObjectIdentity.from_dict(value) for value in raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        current = tuple(identity for identity, _ in _candidates(query, actor))
        if not saved or saved != current:
            raise SemanticChoiceError('The affected graveyard or its incarnations changed')
        if not isinstance(response, Mapping):
            raise SemanticChoiceError('Graveyard exile response must be a card selection')
        selected = response.get('objects')
        transport_fields = {'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema'}
        if (set(response) - transport_fields != {'objects'}
                or not isinstance(selected, (list, tuple)) or len(selected) != 1
                or type(selected[0]) is not str or selected[0] not in {identity.ref for identity in current}):
            raise SemanticChoiceError('Choose exactly one of your current graveyard cards')
        if spec.result is not None:
            from ..semantic_runtime.graveyard_exile_result_intent import GraveyardExileLifeResultIntent
            identity = next(value for value in current if value.ref == selected[0])
            return SemanticChoiceCompletion(intents=(GraveyardExileLifeResultIntent(
                actor=actor, object_refs=tuple(selected), expected_zones=('graveyard',), destination='exile',
                reason=label, owned_only=True, selected_identity=identity.logical_object_id, result=spec.result),))
        return SemanticChoiceCompletion(intents=(MoveObjectsSimultaneouslyIntent(
            actor=actor, object_refs=tuple(selected), expected_zones=('graveyard',), destination='exile',
            reason=label, owned_only=True),))
