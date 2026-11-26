from __future__ import annotations

"""Fixed resolution output through the existing mana choice and intent owners."""

from typing import Mapping
from ..resolution_mana_model import ResolutionManaChoiceSpec
from ..variable_resolution_mana_model import (
    OPTIONAL_TARGET_MANA_CHOICE_VERSION, VariableResolutionManaChoiceSpec, is_optional_target_mana_choice,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import AddManaIntent
from .model import (ManaBundleChoice,ScalarChoice,SemanticChoiceError,SemanticChoiceRequest,
    SemanticChoicePreparation,SemanticChoiceCompletion,AutoContinue)


def _validated(effect,query,*,continuation=False):
    try:spec=(VariableResolutionManaChoiceSpec.from_effect(effect,continuation=continuation,resolved=True)
        if effect.get('schema_version') in {3, OPTIONAL_TARGET_MANA_CHOICE_VERSION}
        else ResolutionManaChoiceSpec.from_effect(effect,continuation=continuation))
    except (TypeError,ValueError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
    actor=effect['_choice_actor'] if continuation else effect['player']
    if actor not in query.active_seats or effect['player']!=actor or continuation and effect['source']!=effect['_source_ref']:
        raise SemanticChoiceError('Resolution mana payer or source identity changed')
    return spec,actor


def prepare_resolution_mana(effect,context):
    spec,actor=_validated(effect,context.query)
    source_ref = context.source_ref
    if source_ref is None and isinstance(spec, VariableResolutionManaChoiceSpec):
        source_ref = context.stack_ref
    if actor!=context.actor or effect['source']!=source_ref:
        raise SemanticChoiceError('Resolution mana controller or source does not match its parent')
    colors = spec.colors
    if spec.amount==0:return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),auto_continue=AutoContinue(reason='Zero mana requires no color choice'))
    optional = isinstance(spec, VariableResolutionManaChoiceSpec) and spec.optional
    scalar_values = (*colors, "decline") if optional else colors
    choice=(ScalarChoice(field_name='choice',legal_values=scalar_values) if spec.selection=='one_color'
        else ManaBundleChoice(field_name='mana',colors=colors,amount=spec.amount,schema_version=2 if isinstance(spec,VariableResolutionManaChoiceSpec) else 1))
    return SemanticChoicePreparation(request=SemanticChoiceRequest(
        prompt=('Choose a color to add the mana or decline.' if optional else
            'Choose the color of the mana.' if spec.selection=='one_color' else 'Choose how to divide the mana among colors.'),
        choice=choice,public_context=FrozenMap({'stack':context.stack_ref,'operation':'choose_mana',
            'selection':spec.selection,'amount':spec.amount,'colors':colors})),
        continuation_effect=FrozenMap({**dict(effect),'_choice_actor':actor,'_source_ref':source_ref}))


def record_optional_target_mana_committed(host, item, actor, effect, intent, result):
    if not is_optional_target_mana_choice(effect):
        return
    if not isinstance(intent, AddManaIntent) or intent.player != actor or type(result) is not int or result < 0:
        raise SemanticChoiceError("Optional target mana requires its exact committed mana result")
    if result:
        from ..rules.trigger_limits import commit_optional_result_usage

        try:
            commit_optional_result_usage(host, item, actor)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc


def complete_resolution_mana(continuation,response,query):
    effect=continuation.effect;spec,actor=_validated(effect,query,continuation=True)
    field='choice' if spec.selection=='one_color' else 'mana'
    allowed={field,'action','action_id','a','cap','reason','plan','next','choice_schema'}
    if set(response)-allowed:raise SemanticChoiceError('Resolution mana response has unknown fields')
    value=response.get(field)
    colors = spec.colors
    if spec.selection=='one_color':
        if isinstance(spec, VariableResolutionManaChoiceSpec) and spec.optional and value == "decline":
            return SemanticChoiceCompletion()
        if type(value) is not str or value not in colors:raise SemanticChoiceError('Choose one of the represented mana colors')
        bundle={value:spec.amount}
    else:
        if (not isinstance(value,Mapping) or not value or not set(value)<=set(colors)
                or any(type(n) is not int or n<0 for n in value.values()) or sum(value.values())!=spec.amount):
            raise SemanticChoiceError('Mana allocation must contain exactly the printed amount in colors')
        bundle=value
    return SemanticChoiceCompletion(intents=tuple(AddManaIntent(player=actor,color=color,amount=bundle[color],
        actor=actor,reason='Fixed resolution mana choice',source_ref=effect['source'])
        for color in colors if bundle.get(color,0)))
