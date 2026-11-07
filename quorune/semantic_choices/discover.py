from __future__ import annotations

"""One-at-a-time exile scanning over the existing zone-intent continuation."""

from dataclasses import dataclass

from ..discover_model import (
    DISCOVER_CAPABILITY,DISCOVER_OPERATION,DISCOVER_STEP_OPERATION,DISCOVER_CAST_OPERATION,
    DISCOVER_FINISH_OPERATION, NONLAND_SCAN_OPERATION, NONLAND_SCAN_STEP_OPERATION, NONLAND_SCAN_CAST_OPERATION,
    ExileCastProgress, discover_spell_qualifies, discover_zone_counter,
    require_current_discover_cards, validate_discover_effect, validate_nonland_scan_effect,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import AutoContinue,SemanticChoiceError,SemanticChoicePreparation


def _identity(row):
    return LibrarySelectionObjectIdentity(row.object_id,row.logical_object_id,row.ref)


def _automatic(effect, follow=None, intents=(), *, reason='Continue the current Discover procedure.'):
    return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),
        preparation_intents=tuple(intents),auto_continue=AutoContinue(
            reason=reason, prepend_effects=(FrozenMap(follow),) if follow is not None else ()))


def _after_pending(progress,pending,query,actor):
    if progress.candidate_ref is not None:
        raise SemanticChoiceError('Discover scanning cannot continue after finding its candidate')
    require_current_discover_cards(progress,query,actor=actor)
    card=LibrarySelectionObjectIdentity.from_dict(pending)
    if any(old.object_id==card.object_id for old in progress.exiled):
        raise SemanticChoiceError('Discover attempted to exile the same card twice')
    row=query.object(card.ref,zones=('exile',))
    if row is None:
        if query.object(card.ref,zones=('library',)) is not None:
            raise SemanticChoiceError('Discover exile made no progress')
        return progress
    expected=f'{card.object_id}@{discover_zone_counter(card)+1}'
    if row.owner!=actor or row.object_id!=card.object_id or row.logical_object_id!=expected:
        raise SemanticChoiceError('The current Discover exile incarnation changed')
    candidate = 'land' not in row.types and (
        progress.amount is None or discover_spell_qualifies(progress.amount, mana_value=row.mana_value)
    )
    return ExileCastProgress(progress.amount, (*progress.exiled, _identity(row)), row.ref if candidate else None)


def _scan(effect,context,progress):
    require_current_discover_cards(progress,context.query,actor=context.actor)
    nonland = progress.amount is None
    reason = 'Continue exiling through the first nonland card.' if nonland else 'Continue the current Discover procedure.'
    if progress.candidate_ref is not None:
        return _automatic(effect, {'op': NONLAND_SCAN_CAST_OPERATION if nonland else DISCOVER_CAST_OPERATION,
                                   'player': context.actor, 'progress': progress.to_dict()}, reason=reason)
    refs=context.query.library_refs(context.actor,top_first=True)
    if not refs:
        if nonland:
            return _automatic(effect, reason='The exile scan found no nonland card.')
        return _automatic(effect,{'op':DISCOVER_FINISH_OPERATION,'player':context.actor,
            'progress':progress.to_dict()})
    row=context.query.object(refs[0],zones=('library',))
    if row is None or row.owner!=context.actor or row.token:
        raise SemanticChoiceError('Discover requires an owned physical library top')
    card=_identity(row)
    move=ZoneMoveIntent(context.actor,card.ref,('library',),'exile',
        'Exile through the first nonland card' if nonland else 'Discover',owned_only=True,
        expected_zone_change_counter=discover_zone_counter(card))
    return _automatic(effect, {'op': NONLAND_SCAN_STEP_OPERATION if nonland else DISCOVER_STEP_OPERATION,
                               'schema_version': 1, 'player': context.actor,
                               'progress': progress.to_dict(), 'pending': card.to_dict()}, (move,), reason=reason)


@dataclass(frozen=True,slots=True)
class DiscoverScanHandler:
    operation: str=DISCOVER_OPERATION
    handler_id: str='choice.library.discover.v1'
    schema_version: int=1
    rule_references: tuple[str,...]=('107.1b','202.3','400.7','608.2g','608.2h','701.57a','701.57b','701.57c')
    capability_dependencies: tuple[str,...]=(DISCOVER_CAPABILITY,)
    continuation_fields: tuple[str,...]=('schema_version','player','amount','progress','pending')
    private_data: tuple[str,...]=('unexiled library top',)
    projected_fields: tuple[str,...]=()
    mutation_path: tuple[str,...]=('ZoneMoveIntent','ZoneTransitionOwner.move_card')
    replay_fixture: str='discover-controller-resumable-exile'
    test_modules: tuple[str,...]=('tests.test_discover_rules',)

    def prepare(self,effect,context):
        try:
            if (effect.get('op')!=self.operation or effect.get('player')!=context.actor
                    or context.actor!=context.stack_controller or context.actor not in context.query.active_seats):
                raise SemanticChoiceError('Discover requires the resolving controller')
            if self.operation==DISCOVER_OPERATION:
                validate_discover_effect(effect,resolved=True)
                progress = ExileCastProgress(effect['amount'], (), None)
            elif self.operation == NONLAND_SCAN_OPERATION:
                validate_nonland_scan_effect(effect, resolved=True)
                progress = ExileCastProgress(None, (), None)
            elif self.operation in {DISCOVER_STEP_OPERATION, NONLAND_SCAN_STEP_OPERATION}:
                if (set(effect)!={'op','schema_version','player','progress','pending'}
                        or type(effect['schema_version']) is not int or effect['schema_version']!=1):
                    raise SemanticChoiceError('Discover step has a closed continuation shape')
                progress = ExileCastProgress.from_dict(effect['progress'])
                if (progress.amount is None) != (self.operation == NONLAND_SCAN_STEP_OPERATION):
                    raise SemanticChoiceError('The exile scan changed its printed search rule')
                progress = _after_pending(progress, effect['pending'], context.query, context.actor)
            else:raise SemanticChoiceError('Unknown Discover scan operation')
            return _scan(effect,context,progress)
        except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('Discover scanning accepts no player-selected cards')
