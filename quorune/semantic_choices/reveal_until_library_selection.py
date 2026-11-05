from __future__ import annotations

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..reveal_until_library_selection_model import (
    REVEAL_UNTIL_OPERATION,REVEAL_UNTIL_CAPABILITY,fixed_selection_after_reveal_until,
)
from .model import AutoContinue,SemanticChoicePreparation,SemanticChoiceError


def _all_revealed_hand_intents(fixed,context):
    from ..rules.library_selection import LibrarySelectionArrangement,LibrarySelectionObjectIdentity
    from ..semantic_runtime.intents import LibrarySelectionIntent,RevealLibraryCardsIntent
    refs = context.query.library_refs(context.actor,top_first=True)[:fixed['look_count']]
    rows = [context.query.object(ref,zones=('library',)) for ref in refs]
    looked = tuple(LibrarySelectionObjectIdentity(row.object_id,row.logical_object_id,row.ref) for row in rows)
    arrangement = LibrarySelectionArrangement(looked,refs,(),'graveyard','chosen')
    return (RevealLibraryCardsIntent(actor=context.actor,player=context.actor,viewer=context.actor,
                refs_top_first=refs,reason=context.stack_label,public=True),
        LibrarySelectionIntent(actor=context.actor,player=context.actor,arrangement=arrangement,
            reason=context.stack_label,source_stack_ref=context.stack_ref,
            looked_are_public=True,selected_are_public=True))


@dataclass(frozen=True,slots=True)
class RevealUntilLibrarySelectionHandler:
    operation: str = REVEAL_UNTIL_OPERATION
    handler_id: str = 'choice.library.reveal-until-selection.v1'
    schema_version: int = 1
    rule_references: tuple[str,...] = ('401.4','608.2c','609.3','701.20a','701.20b','701.20d')
    capability_dependencies: tuple[str,...] = (REVEAL_UNTIL_CAPABILITY,'library.select.fixed_controller')
    continuation_fields: tuple[str,...] = ('schema_version','player','stop_count','predicate_groups',
        'selection','selected_destination','entry_tapped','remainder_destination','remainder_order')
    private_data: tuple[str,...] = ('library prefix before the owned public reveal',)
    projected_fields: tuple[str,...] = ()
    mutation_path: tuple[str,...] = ('fixed_library_selection','RevealLibraryCardsIntent','LibrarySelectionIntent')
    replay_fixture: str = 'reveal-until-library-selection'
    test_modules: tuple[str,...] = ('tests.test_reveal_until_library_selection',)

    def prepare(self,effect,context):
        if (effect.get('player')!=context.actor or context.stack_controller!=context.actor
                or context.actor not in context.query.active_seats):
            raise SemanticChoiceError('Reveal-until requires the resolving live controller')
        try:fixed = fixed_selection_after_reveal_until(effect,context.query)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        all_revealed = fixed is not None and effect['selection']=='all_revealed'
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
            preparation_intents=_all_revealed_hand_intents(fixed,context) if all_revealed else (),
            auto_continue=AutoContinue(reason='The stopping prefix determines the owned library partition.',
                prepend_effects=(FrozenMap(fixed),) if fixed is not None and not all_revealed else ()))

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('The reveal-until prefix is not a player-selected count')
