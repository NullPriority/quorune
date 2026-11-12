from __future__ import annotations

"""Version-2 payload support for the existing registered payment choice."""

from typing import Any, Mapping
from dataclasses import replace

from ..fixed_effect_payment import RETURN_PAYMENT_KIND, FixedEffectPaymentSpec
from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap
from ..rules.blight_model import blight_decline_branch_is_closed
from ..rules.hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_KIND
from ..semantic_runtime.intents import MoveObjectsSimultaneouslyIntent, PayLifeIntent, PayManaCostIntent, SetPermanentsTappedIntent, RemoveCountersIntent
from ..semantic_runtime.energy_payment import PayEnergyIntent
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue, ObjectChoice, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceContinuation,
    SemanticChoiceError, SemanticChoicePreparation, SemanticChoiceRequest,
)
from .optional_effect import _represented_effect
from .blight import blight_creature_can_receive, blight_placement_intent
from .controlled_return import controlled_return_intent

_CONTROLLED_OBJECT_PAYMENTS = frozenset({'sacrifice', 'tap', 'blight', RETURN_PAYMENT_KIND})
_OBJECT_PAYMENT_KINDS = _CONTROLLED_OBJECT_PAYMENTS | {'discard', 'exile', 'source_sacrifice'}


def _object_payment_count(spec):
    return 1 if spec.kind == "blight" else spec.amount


def _object_matches_payment(row, spec, actor, query):
    return object_matches_query(row, spec.predicate) and (
        spec.kind != "blight" or blight_creature_can_receive(row, actor, spec.amount, query)
    )


def _object_payment_zones(spec):
    return ('battlefield',) if spec.kind=='source_sacrifice' else spec.predicate.zones


def _source_sacrifice_payment_row(spec,actor,query):
    row=query.object(spec.source_ref,zones=('battlefield',)) if spec.source_ref is not None else None
    return row if row is not None and not row.phased_out and row.controller==actor else None


def _validated(
    effect: Mapping[str, Any], actor: str, query: SemanticChoiceQuery,
    *, continuation: bool = False,
) -> tuple[FixedEffectPaymentSpec, tuple[FrozenMap, ...]]:
    fields = {'op', 'schema_version', 'player', 'payment', 'effects'}
    if "declined_effects" in effect:
        fields.add("declined_effects")
    local = {'_choice_actor', '_legal_objects', '_stack_label'} if continuation else set()
    if continuation and isinstance(effect.get('payment'), Mapping) and effect['payment'].get('kind') == HYBRID_RESOLUTION_PAYMENT_KIND:
        local.add('_legal_mana_options')
    if (
        set(effect) not in (fields | local, fields | local | {'cost'})
        or effect.get('op') != 'offer_optional_mana_payment'
        or type(effect.get('schema_version')) is not int
        or effect['schema_version'] != 2
    ):
        raise SemanticChoiceError('Fixed effect payment fields are malformed')
    if effect['player'] != actor or actor not in query.active_seats:
        raise SemanticChoiceError('Fixed payment controller changed')
    try:
        spec = FixedEffectPaymentSpec.from_dict(effect['payment'], allow_mandatory=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if ('cost' in effect) != (spec.kind == 'mana') or spec.kind == 'mana' and dict(effect['cost']) != dict(spec.requirements):
        raise SemanticChoiceError('Fixed payment mana identity changed')
    if (spec.kind in {'discard','exile','mana_discard'} and spec.predicate.owner != actor
            or spec.kind in _CONTROLLED_OBJECT_PAYMENTS and spec.predicate.controller != actor):
        raise SemanticChoiceError('Fixed payment object relation changed')
    effects = effect['effects']
    if "declined_effects" in effect:
        if not blight_decline_branch_is_closed(spec.kind, effects, effect["declined_effects"]):
            raise SemanticChoiceError("The declined Blight or mana result is malformed")
        effects = effect["declined_effects"]
    if not isinstance(effects, (tuple, list)) or not 1 <= len(effects) <= 4:
        raise SemanticChoiceError('Fixed payment needs closed typed consequences')
    consequences = []
    for nested in effects:
        if not isinstance(nested, Mapping) or nested.get('op') in {'offer_optional_effect', 'offer_optional_mana_payment'}:
            raise SemanticChoiceError('Fixed payments cannot nest')
        if (spec.kind == RETURN_PAYMENT_KIND and nested.get("op") == "grant_declaration_restriction_until_end_of_turn"
                and "card" in nested and nested["card"] is None):
            continue
        _represented_effect(nested, actor=actor, query=query, allow_event_player_results=True)
        consequences.append(FrozenMap(nested))
    return spec, tuple(consequences)


def prepare_fixed_effect_payment(
    effect: Mapping[str, Any], context: SemanticChoiceContext,
) -> SemanticChoicePreparation:
    actor = context.actor
    spec, _ = _validated(effect, actor, context.query)
    return prepare_fixed_payment_cost(spec, effect, context)


def _source_counter_payment_row(spec,query):
    row=query.object(spec.source_ref,zones=('battlefield',)) if spec.source_ref is not None else None
    if row is None or row.phased_out:return None
    count=row.counters.get(spec.counter_name,0)
    if type(count) is not int or count<spec.amount:return None
    return row


def _source_counter_payment_intent(spec,effect,query):
    row=_source_counter_payment_row(spec,query)
    legal={value['ref']:value['logical_object_id'] for value in effect['_legal_objects']}
    if row is None or legal.get(row.ref)!=row.logical_object_id:
        raise SemanticChoiceError('Source counter payment is unavailable or changed incarnation')
    return RemoveCountersIntent(actor=effect['_choice_actor'],object_ref=row.ref,counter_name=spec.counter_name,
        amount=spec.amount,reason=effect['_stack_label'],source_ref=row.ref)


def prepare_fixed_payment_cost(
    spec: FixedEffectPaymentSpec, effect: Mapping[str, Any],
    context: SemanticChoiceContext,
) -> SemanticChoicePreparation:
    """Publish the existing closed cost independently of its result branch."""
    if spec.kind == HYBRID_RESOLUTION_PAYMENT_KIND:
        from .hybrid_payment import prepare_hybrid_payment

        return prepare_hybrid_payment(spec, effect, context)
    actor = context.actor
    if spec.kind=='mana_discard':
        prepared=prepare_fixed_payment_cost(FixedEffectPaymentSpec('discard',predicate=spec.predicate),effect,context)
        payable=context.query.cost_is_affordable(actor,spec.requirements) and prepared.request.public_context['payable']
        choice=prepared.request.choice
        if not payable:choice=replace(choice,maximum=0,legal_refs=())
        request=replace(prepared.request,prompt='Pay the fixed mana and discard one card, or decline.',choice=choice,
            public_context=FrozenMap({**dict(prepared.request.public_context),'payment_kind':'mana_discard',
                'payable':payable,'cost':dict(spec.requirements)}))
        return replace(prepared,request=request)
    legal = ()
    if spec.kind in _OBJECT_PAYMENT_KINDS:
        required = _object_payment_count(spec)
        if spec.kind=='source_sacrifice':
            source=_source_sacrifice_payment_row(spec,actor,context.query)
            legal=(source,) if source is not None else ()
        else:
            candidates = context.query.objects(
                zones=spec.predicate.zones,
                owner=actor if spec.kind in {'discard','exile'} else None,
                controller=actor if spec.kind in _CONTROLLED_OBJECT_PAYMENTS else None,
            )
            legal = tuple(sorted(
                (row for row in candidates if _object_matches_payment(row, spec, actor, context.query)),
                key=lambda row: (row.ref, row.logical_object_id),
            ))
        payable = len(legal) >= required
        if not payable and (spec.mandatory or spec.kind == RETURN_PAYMENT_KIND):
            reason = "Mandatory discard is unavailable" if spec.mandatory else "No eligible permanent to return"
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason),
            )
        choice = ObjectChoice(
            field_name='cards', legal_refs=tuple(row.ref for row in legal),
            zones=_object_payment_zones(spec), minimum=required if spec.mandatory else 0,
            maximum=required if payable else 0, optional=not spec.mandatory,
            visibility='actor_private' if spec.kind == 'discard' else 'public',
            allowed_cardinalities=((0, required) if payable else (0,)) if required > 1 else None,
            owner_relation='actor' if spec.kind in {'discard','exile'} else 'any',
            controller_relation='actor' if spec.kind in _CONTROLLED_OBJECT_PAYMENTS or spec.kind == 'source_sacrifice' else 'any',
        )
    elif spec.kind=='remove_source_counter':
        source=_source_counter_payment_row(spec,context.query)
        legal=(source,) if source is not None else ()
        payable=bool(legal)
        choice=ScalarChoice(field_name='pay',legal_values=(True,False) if payable else (False,))
    else:
        payable = (
            context.query.player_life(actor) >= spec.amount if spec.kind == 'life'
            else context.query.player_counter(actor, 'energy') >= spec.amount if spec.kind == 'energy'
            else context.query.cost_is_affordable(actor, spec.requirements)
        )
        choice = ScalarChoice(field_name='pay', legal_values=(True, False) if payable else (False,))
    stored = {
        **dict(effect), '_choice_actor': actor, '_stack_label': context.stack_label,
        '_legal_objects': [{'ref': row.ref, 'logical_object_id': row.logical_object_id} for row in legal],
    }
    public = {'stack': context.stack_ref, 'operation': effect['op'], 'payment_kind': spec.kind, 'payable': payable}
    if spec.kind == 'discard':
        public['objects'] = [{'id': row.ref, 'name': row.printed_name} for row in legal]
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=("Choose a card to discard." if spec.mandatory else "Pay the optional fixed cost or decline."),
            choice=choice,
            public_context=FrozenMap(public),
        ),
        continuation_effect=FrozenMap(stored),
    )


def complete_fixed_effect_payment(
    continuation: SemanticChoiceContinuation, response: Mapping[str, Any],
    query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    effect = continuation.effect
    actor = effect.get('_choice_actor')
    spec, consequences = _validated(effect, actor, query, continuation=True)
    completion = complete_fixed_payment_cost(spec, effect, response, query)
    run_consequences = bool(completion.intents) != ("declined_effects" in effect)
    return SemanticChoiceCompletion(
        intents=completion.intents,
        prepend_effects=consequences if run_consequences else (),
    )


def _object_payment_intent(spec, actor, selected, label):
    if spec.kind == RETURN_PAYMENT_KIND:
        return controlled_return_intent(actor, selected, label)
    if spec.kind == "blight":
        return blight_placement_intent(actor, selected[0], spec.amount, label, effect_generated=False)
    if spec.kind == "tap":
        return SetPermanentsTappedIntent(actor=actor, object_refs=selected, tapped=True, reason=label)
    return MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=selected, expected_zones=_object_payment_zones(spec),
        destination='exile' if spec.kind=='exile' else 'graveyard', reason=label,
        transition_kind={'discard':ZoneTransitionKind.DISCARD,'sacrifice':ZoneTransitionKind.SACRIFICE,
            'source_sacrifice':ZoneTransitionKind.SACRIFICE,'exile':ZoneTransitionKind.ORDINARY}[spec.kind],
        owned_only=spec.kind in {'discard','exile'}, controlled_only=spec.kind in {'sacrifice','source_sacrifice'},
    )


def complete_fixed_payment_cost(
    spec: FixedEffectPaymentSpec, effect: Mapping[str, Any],
    response: Mapping[str, Any], query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    """Validate and commit one fixed cost through the canonical intent owner."""
    if spec.kind == HYBRID_RESOLUTION_PAYMENT_KIND:
        from .hybrid_payment import complete_hybrid_payment

        return complete_hybrid_payment(spec, effect, response, query)
    actor = effect['_choice_actor']
    label = effect['_stack_label']
    if spec.kind=='mana_discard':
        return _complete_mana_discard_cost(spec,effect,response,query)
    if spec.kind in _OBJECT_PAYMENT_KINDS:
        unknown = set(response) - {'cards', 'pay', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema'}
        if unknown:
            raise SemanticChoiceError('Fixed object payment response has unknown fields: ' + ','.join(sorted(unknown)))
        raw = response.get('cards', ())
        if (
            not isinstance(raw, (list, tuple)) or len(raw) not in {0, _object_payment_count(spec)}
            or any(type(ref) is not str for ref in raw) or len(set(raw)) != len(raw)
        ):
            raise SemanticChoiceError('Fixed object payment selection is malformed')
        selected = tuple(raw)
        if 'pay' in response and type(response['pay']) is not bool:
            raise SemanticChoiceError('Fixed payment flag must be boolean')
        if not selected:
            if spec.mandatory or response.get('pay', False):
                raise SemanticChoiceError('Fixed payment requires its complete selected cost')
            return SemanticChoiceCompletion()
        if response.get('pay', True) is not True:
            raise SemanticChoiceError('Selected payment conflicts with decline')
        legal = {row['ref']: row['logical_object_id'] for row in effect['_legal_objects']}
        for ref in selected:
            current = (_source_sacrifice_payment_row(spec,actor,query) if spec.kind=='source_sacrifice'
                else query.object(ref,zones=spec.predicate.zones))
            if (ref not in legal or current is None or spec.kind=='source_sacrifice' and current.ref!=ref or current.logical_object_id != legal[ref]
                    or spec.kind!='source_sacrifice' and not _object_matches_payment(current,spec,actor,query)):
                raise SemanticChoiceError('Fixed payment object is stale or ineligible')
        intent = _object_payment_intent(spec, actor, selected, label)
    else:
        unknown = set(response) - {'pay', 'action', 'action_id', 'a', 'cap', 'reason', 'plan', 'next', 'choice_schema'}
        if unknown:
            raise SemanticChoiceError('Fixed resource payment response has unknown fields: ' + ','.join(sorted(unknown)))
        if type(response.get('pay', False)) is not bool:
            raise SemanticChoiceError('Fixed resource payment choice must be boolean')
        if not response.get('pay', False):
            return SemanticChoiceCompletion()
        if spec.kind=='remove_source_counter':
            intent=_source_counter_payment_intent(spec,effect,query)
        elif spec.kind == 'life':
            if query.player_life(actor) < spec.amount:
                raise SemanticChoiceError('Fixed life payment is no longer payable')
            intent = PayLifeIntent(actor=actor, player=actor, amount=spec.amount, reason=label)
        elif spec.kind == 'energy':
            if query.player_counter(actor, 'energy') < spec.amount:
                raise SemanticChoiceError('Fixed energy payment is no longer payable')
            intent = PayEnergyIntent(actor=actor, player=actor, amount=spec.amount, reason=label)
        else:
            if not query.cost_is_affordable(actor, spec.requirements):
                raise SemanticChoiceError('Fixed mana payment is no longer payable')
            intent = PayManaCostIntent(
                actor=actor, player=actor, requirements=spec.requirements, reason=label,
                event_code='effect.optional_mana.paid', message='Optional fixed effect cost paid.',
                details=FrozenMap({'cost': dict(spec.requirements)}),
            )
    return SemanticChoiceCompletion(intents=(intent,))


def _complete_mana_discard_cost(spec,effect,response,query):
    discard=complete_fixed_payment_cost(FixedEffectPaymentSpec('discard',predicate=spec.predicate),effect,response,query)
    if not discard.intents:return discard
    actor=effect['_choice_actor']
    if not query.cost_is_affordable(actor,spec.requirements):
        raise SemanticChoiceError('Combined mana/discard payment is no longer payable')
    mana=PayManaCostIntent(actor=actor,player=actor,requirements=spec.requirements,reason=effect['_stack_label'],
        event_code='effect.optional_mana.paid',message='Optional mana and discard cost paid.',
        details=FrozenMap({'cost':dict(spec.requirements),'payment_kind':'mana_discard'}))
    return SemanticChoiceCompletion(intents=(mana,*discard.intents))
