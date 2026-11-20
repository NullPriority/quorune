"""Actual one-card exile results feed the existing finite-set cast choice."""
from math import isfinite
from ..mana_value_exile_model import TOTAL_MANA_EXILE_SCOPE, ManaValueExileProgress
from ..rules.mana_value_exile import validate_total_mana_instruction,validate_total_mana_step
from ..rules.library_selection import LibrarySelectionObjectIdentity
from ..discover_model import discover_zone_counter
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.zone_move_intent import ZoneMoveIntent
from .model import AutoContinue,SemanticChoiceError,SemanticChoicePreparation


def _automatic(effect,follow=None,intents=()):
    return SemanticChoicePreparation(None,FrozenMap(effect),preparation_intents=tuple(intents),
        auto_continue=AutoContinue('Continue the current total mana-value exile instruction.',
            prepend_effects=(FrozenMap(follow),) if follow is not None else ()))


def _append_actual_exile(progress,pending,query,actor):
    row=query.object(pending.ref,zones=('exile',))
    if row is None:
        if query.object(pending.ref,zones=('library',)) is not None:
            raise SemanticChoiceError('Total mana-value exile made no progress')
        return progress
    expected=f'{pending.object_id}@{discover_zone_counter(pending)+1}'
    amount=row.mana_value
    if (row.owner!=actor or row.token or row.object_id!=pending.object_id or row.logical_object_id!=expected
            or type(amount) not in (int,float) or not isfinite(amount) or amount<0 or int(amount)!=amount):
        raise SemanticChoiceError('Total mana-value exile result lost its exact incarnation or integral value')
    return progress.append(LibrarySelectionObjectIdentity(row.object_id,row.logical_object_id,row.ref),int(amount))


def prepare_total_mana_exile(effect,context):
    from ..fixed_top_exile import FIXED_TOP_EXILE_OPERATION,FIXED_TOP_EXILE_OFFER_OPERATION
    if effect.get('player')!=context.actor or context.actor!=context.stack_controller or context.actor not in context.query.active_seats:
        raise SemanticChoiceError('Total mana-value exile belongs to its resolving controller')
    if effect.get('schema_version')==4:
        validate_total_mana_instruction(effect,resolved=True)
        instruction=effect;progress=ManaValueExileProgress(effect['minimum_total'])
    else:
        progress,pending=validate_total_mana_step(effect)
        instruction=effect['instruction']
        progress=_append_actual_exile(progress,pending,context.query,context.actor)
    for card in progress.cards:
        row=context.query.object(card.identity.ref,zones=('exile',))
        if (row is None or row.owner!=context.actor or row.object_id!=card.identity.object_id
                or row.logical_object_id!=card.identity.logical_object_id or row.token):
            raise SemanticChoiceError('An actual total mana-value exile prefix changed incarnation')
    refs=context.query.library_refs(context.actor,top_first=True)
    if progress.complete or not refs:
        results=[{'owner':context.actor,'identity':card.identity.to_dict()} for card in progress.cards]
        return _automatic(effect,{'op':FIXED_TOP_EXILE_OFFER_OPERATION,'instruction':instruction,'results':results})
    row=context.query.object(refs[0],zones=('library',))
    if row is None or row.owner!=context.actor or row.token or any(c.identity.object_id==row.object_id for c in progress.cards):
        raise SemanticChoiceError('Total mana-value exile requires the next unique physical library top')
    pending=LibrarySelectionObjectIdentity(row.object_id,row.logical_object_id,row.ref)
    move=ZoneMoveIntent(context.actor,pending.ref,('library',),'exile','Exile through the printed total mana value',
        owned_only=True,expected_zone_change_counter=discover_zone_counter(pending))
    step={'op':FIXED_TOP_EXILE_OPERATION,'schema_version':5,'player':context.actor,'scope':TOTAL_MANA_EXILE_SCOPE,
          'instruction':instruction,'progress':progress.to_dict(),'pending':pending.to_dict()}
    return _automatic(effect,step,(move,))
