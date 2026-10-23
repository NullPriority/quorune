from __future__ import annotations

"""Fixed resolution output through the existing mana choice and intent owners."""

from typing import Mapping
from ..resolution_mana_model import MANA_COLORS,ResolutionManaChoiceSpec
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import AddManaIntent
from .model import (ManaBundleChoice,ScalarChoice,SemanticChoiceError,SemanticChoiceRequest,
    SemanticChoicePreparation,SemanticChoiceCompletion)


def _validated(effect,query,*,continuation=False):
    try:spec=ResolutionManaChoiceSpec.from_effect(effect,continuation=continuation)
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    actor=effect['_choice_actor'] if continuation else effect['player']
    if actor not in query.active_seats or effect['player']!=actor or continuation and effect['source']!=effect['_source_ref']:
        raise SemanticChoiceError('Resolution mana payer or source identity changed')
    return spec,actor


def prepare_resolution_mana(effect,context):
    spec,actor=_validated(effect,context.query)
    if actor!=context.actor or effect['source']!=context.source_ref:
        raise SemanticChoiceError('Resolution mana controller or source does not match its parent')
    choice=(ScalarChoice(field_name='choice',legal_values=MANA_COLORS) if spec.selection=='one_color'
        else ManaBundleChoice(field_name='mana',colors=MANA_COLORS,amount=spec.amount))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt='Choose the color of the mana.' if spec.selection=='one_color' else 'Choose how to divide the mana among colors.',
        choice=choice,public_context=FrozenMap({'stack':context.stack_ref,'operation':'choose_mana',
            'selection':spec.selection,'amount':spec.amount,'colors':MANA_COLORS})),
        continuation_effect=FrozenMap({**dict(effect),'_choice_actor':actor,'_source_ref':context.source_ref}))


def complete_resolution_mana(continuation,response,query):
    effect=continuation.effect;spec,actor=_validated(effect,query,continuation=True)
    field='choice' if spec.selection=='one_color' else 'mana'
    allowed={field,'action','action_id','a','cap','reason','plan','next','choice_schema'}
    if set(response)-allowed:raise SemanticChoiceError('Resolution mana response has unknown fields')
    value=response.get(field)
    if spec.selection=='one_color':
        if type(value) is not str or value not in MANA_COLORS:raise SemanticChoiceError('Choose one of the five mana colors')
        bundle={value:spec.amount}
    else:
        if (not isinstance(value,Mapping) or not value or not set(value)<=set(MANA_COLORS)
                or any(type(n) is not int or n<0 for n in value.values()) or sum(value.values())!=spec.amount):
            raise SemanticChoiceError('Mana allocation must contain exactly the printed amount in colors')
        bundle=value
    return SemanticChoiceCompletion(intents=tuple(AddManaIntent(player=actor,color=color,amount=bundle[color],
        actor=actor,reason='Fixed resolution mana choice',source_ref=effect['source'])
        for color in MANA_COLORS if bundle.get(color,0)))
