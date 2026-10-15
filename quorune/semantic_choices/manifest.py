from __future__ import annotations

"""Principal-private selection and sequential Manifest entry continuation."""

from dataclasses import dataclass

from ..manifest import MANIFEST_CAPABILITY, MANIFEST_DREAD_CAPABILITY
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import RevealLibraryCardsIntent
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import (AutoContinue, ObjectChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest)


def _manifest_intent(row, actor, reason):
    return ZoneMoveIntent(actor=actor, object_ref=row.ref, expected_zones=('library',),
        destination='battlefield', new_controller=actor, reason=reason,
        expected_zone_change_counter=int(row.logical_object_id.rsplit('@', 1)[1]), manifest_entry=True)


@dataclass(frozen=True, slots=True)
class ManifestChoiceHandler:
    operation: str = 'manifest_card'
    handler_id: str = 'choice.manifest.controller.v1'
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('CR 701.40', 'CR 701.62a', 'CR 708.2', 'CR 708.5')
    capability_dependencies: tuple[str, ...] = (MANIFEST_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ('player', 'mode', '_choice_actor', '_looked_refs', '_looked_incarnations')
    private_data: tuple[str, ...] = ('actor top library identities',)
    projected_fields: tuple[str, ...] = ('prompt', 'objects', 'legal_actions.choice_schema.legal_refs')
    mutation_path: tuple[str, ...] = ('RevealLibraryCardsIntent', 'ZoneMoveIntent manifested entry then remainder graveyard')
    replay_fixture: str = 'manifest-controller-private-choice'
    test_modules: tuple[str, ...] = ('tests.test_manifest',)

    def prepare(self, effect, context):
        if set(effect) != {'op', 'player', 'mode'} or effect['player'] != context.actor or effect['mode'] not in {'top', 'dread'}:
            raise SemanticChoiceError('Manifest needs one current controller and represented mode')
        refs = context.query.library_refs(context.actor, top_first=True)[:2 if effect['mode'] == 'dread' else 1]
        if not refs:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='empty library manifests no card'))
        rows = tuple(context.query.object(ref, zones=('library',)) for ref in refs)
        if any(row is None for row in rows):
            raise SemanticChoiceError('Manifest library snapshot is unavailable')
        if effect['mode'] == 'top':
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                preparation_intents=(_manifest_intent(rows[0], context.actor, context.stack_label),),
                auto_continue=AutoContinue(reason='manifest the original top library card'))
        choice = ObjectChoice(field_name='card', legal_refs=refs, zones=('library',), minimum=1, maximum=1,
            visibility='actor_private')
        return SemanticChoicePreparation(request=SemanticChoiceRequest(prompt='Choose a card to manifest.', choice=choice,
            public_context=FrozenMap({'stack': context.stack_ref, 'objects': [
                {'id': row.ref, 'name': row.printed_name} for row in rows]})),
            continuation_effect=FrozenMap({**dict(effect), '_choice_actor': context.actor,
                '_looked_refs': refs, '_looked_incarnations': [row.logical_object_id for row in rows]}),
            preparation_intents=(RevealLibraryCardsIntent(actor=context.actor, player=context.actor,
                viewer=context.actor, refs_top_first=refs, reason=context.stack_label),))

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = str(effect['_choice_actor'])
        refs = tuple(effect['_looked_refs'])
        submitted = response.get('card')
        if isinstance(submitted, (list, tuple)) and len(submitted) == 1:
            submitted = submitted[0]
        if submitted not in refs:
            raise SemanticChoiceError('Manifest selection must be one original looked-at card')
        rows = tuple(query.object(ref, zones=('library',)) for ref in refs)
        if (any(row is None for row in rows) or tuple(row.logical_object_id for row in rows) != tuple(effect['_looked_incarnations'])
                or query.library_refs(actor, top_first=True)[:len(refs)] != refs):
            raise SemanticChoiceError('Manifest looked-at library incarnations changed')
        chosen = next(row for row in rows if row.ref == submitted)
        remainder = tuple(row for row in rows if row.ref != submitted)
        return SemanticChoiceCompletion(intents=(_manifest_intent(chosen, actor, continuation.note),
            *(ZoneMoveIntent(actor=actor, object_ref=row.ref, expected_zones=('library',), destination='graveyard',
                reason=continuation.note, expected_zone_change_counter=int(row.logical_object_id.rsplit('@', 1)[1]))
                for row in remainder)))


MANIFEST_CHOICE_HANDLERS = (ManifestChoiceHandler(),)
