from __future__ import annotations

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..variable_library_selection_model import (
    VARIABLE_LIBRARY_SELECTION_OPERATION,VARIABLE_LIBRARY_SELECTION_CAPABILITY,
    fixed_selection_for_resolved_count,
)
from .model import AutoContinue,SemanticChoicePreparation,SemanticChoiceError


@dataclass(frozen=True,slots=True)
class VariableLibrarySelectionHandler:
    operation: str = VARIABLE_LIBRARY_SELECTION_OPERATION
    handler_id: str = 'choice.library.variable-selection.v1'
    schema_version: int = 1
    rule_references: tuple[str,...] = ('107.1b','400.2','401.4','608.2c','609.3','701.20')
    capability_dependencies: tuple[str,...] = (VARIABLE_LIBRARY_SELECTION_CAPABILITY,'library.select.fixed_controller')
    continuation_fields: tuple[str,...] = ('schema_version','player','look_count','public_reveal','selected_reveal',
        'selection_policy','minimum','maximum','predicate_groups','remainder_destination','remainder_order')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ()
    mutation_path: tuple[str,...] = ('fixed_library_selection','LibrarySelectionIntent')
    replay_fixture: str = 'variable-library-inspection'
    test_modules: tuple[str,...] = ('tests.test_variable_library_selection',)

    def prepare(self,effect,context):
        if effect.get('player')!=context.actor or context.stack_controller!=context.actor:
            raise SemanticChoiceError('Variable inspection controller changed')
        try:fixed=fixed_selection_for_resolved_count(effect)
        except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason='The resolved count determines the next library instruction.',
                prepend_effects=(FrozenMap(fixed),) if fixed is not None else ()))

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('The count bridge never accepts a player-selected count')
