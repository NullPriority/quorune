from __future__ import annotations

"""One-at-a-time exile scanning over the existing zone-intent continuation."""

from dataclasses import dataclass

from ..discover_model import (
    DISCOVER_CAPABILITY, DISCOVER_OPERATION, DISCOVER_STEP_OPERATION, NONLAND_SCAN_OPERATION, NONLAND_SCAN_STEP_OPERATION,
    QUALIFIED_SCAN_CAPABILITY, QUALIFIED_SCAN_OPERATION, QUALIFIED_SCAN_STEP_OPERATION,
    QUALIFIED_SCAN_CAST_OPERATION, QUALIFIED_SCAN_FINISH_OPERATION,
    ExileCastProgress, discover_spell_qualifies, discover_zone_counter,
    require_current_discover_cards, validate_discover_effect, validate_nonland_scan_effect, validate_qualified_scan_effect,
)
from ..replacement.immutable import FrozenMap
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .discover_cast import DiscoverCastChoiceHandler
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
    if row.owner != (progress.supplier or actor) or row.object_id != card.object_id or row.logical_object_id != expected:
        raise SemanticChoiceError('The current Discover exile incarnation changed')
    if progress.supplier is not None:
        candidate = 'instant' in row.types or 'sorcery' in row.types
    else:
        candidate = 'land' not in row.types and (
            progress.amount is None or discover_spell_qualifies(progress.amount, mana_value=row.mana_value)
        )
    return ExileCastProgress(progress.amount, (*progress.exiled, _identity(row)),
                             row.ref if candidate else None, progress.supplier)


def _scan(effect,context,progress):
    require_current_discover_cards(progress,context.query,actor=context.actor)
    owner = progress.supplier or context.actor
    nonland = progress.finish_operation is None
    reason = 'Continue exiling through the first nonland card.' if nonland else 'Continue the current Discover procedure.'
    if progress.supplier is not None:
        reason = 'Continue exiling through the first instant or sorcery card.'
    if progress.candidate_ref is not None:
        return _automatic(effect, {'op': progress.cast_operation,
                                   'player': context.actor, 'progress': progress.to_dict()}, reason=reason)
    refs=context.query.library_refs(owner,top_first=True)
    if not refs:
        if nonland:
            return _automatic(effect, reason='The exile scan found no nonland card.')
        return _automatic(effect,{'op':progress.finish_operation,'player':context.actor,
            'progress':progress.to_dict()})
    row=context.query.object(refs[0],zones=('library',))
    if row is None or row.owner!=owner or row.token:
        raise SemanticChoiceError('Discover requires an owned physical library top')
    card=_identity(row)
    move_reason = 'Exile through the first nonland card' if nonland else 'Discover'
    if progress.supplier is not None:
        move_reason = 'Exile through the first instant or sorcery card'
    move=ZoneMoveIntent(owner,card.ref,('library',),'exile',move_reason,owned_only=True,
        expected_zone_change_counter=discover_zone_counter(card))
    return _automatic(effect, {'op': progress.step_operation,
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
            elif self.operation == QUALIFIED_SCAN_OPERATION:
                validate_qualified_scan_effect(effect, resolved=True)
                if effect['supplier'] not in context.query.active_seats:
                    return _automatic(effect, reason='The supplying player has left the game.')
                progress = ExileCastProgress(None, (), None, effect['supplier'])
            elif self.operation in {DISCOVER_STEP_OPERATION, NONLAND_SCAN_STEP_OPERATION, QUALIFIED_SCAN_STEP_OPERATION}:
                if (set(effect)!={'op','schema_version','player','progress','pending'}
                        or type(effect['schema_version']) is not int or effect['schema_version']!=1):
                    raise SemanticChoiceError('Discover step has a closed continuation shape')
                progress = ExileCastProgress.from_dict(effect['progress'])
                if self.operation != progress.step_operation:
                    raise SemanticChoiceError('The exile scan changed its printed search rule')
                progress = _after_pending(progress, effect['pending'], context.query, context.actor)
            else:raise SemanticChoiceError('Unknown Discover scan operation')
            return _scan(effect,context,progress)
        except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('Discover scanning accepts no player-selected cards')


_QUALIFIED_SCAN_EVIDENCE = dict(
    rule_references=('400.7j', '608.2c', '608.2d', '608.2g', '701.13a'),
    capability_dependencies=(QUALIFIED_SCAN_CAPABILITY,),
    replay_fixture='supplied-library-exile-cast-and-cleanup',
)
QUALIFIED_SCAN_HANDLERS = (
    DiscoverScanHandler(QUALIFIED_SCAN_OPERATION, 'choice.library.exile-until-instant-or-sorcery.v1',
                        continuation_fields=('schema_version', 'player', 'supplier'), **_QUALIFIED_SCAN_EVIDENCE),
    DiscoverScanHandler(QUALIFIED_SCAN_STEP_OPERATION, 'choice.library.exile-until-instant-or-sorcery-step.v1',
                        **_QUALIFIED_SCAN_EVIDENCE),
    DiscoverCastChoiceHandler(QUALIFIED_SCAN_CAST_OPERATION, 'choice.library.exile-until-instant-or-sorcery-cast.v1',
                              mutation_path=('DiscoverCastIntent',), **_QUALIFIED_SCAN_EVIDENCE),
    DiscoverCastChoiceHandler(QUALIFIED_SCAN_FINISH_OPERATION, 'choice.library.exile-until-instant-or-sorcery-finish.v1',
                              mutation_path=('DiscoverCleanupIntent',), **_QUALIFIED_SCAN_EVIDENCE),
)
