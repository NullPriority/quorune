from __future__ import annotations

"""Version-2 payload support for the existing registered payment choice."""

from typing import Any, Mapping
from dataclasses import replace

from ..fixed_effect_payment import (
    EQUIPPED_DEATH_LIFE_PRICE_VERSION, MANDATORY_ARTIFACT_SACRIFICE_PAYMENT_VERSION, RETURN_PAYMENT_KIND,
    FixedEffectPaymentSpec, mandatory_artifact_sacrifice_result_parts,
)
from ..object_query import object_matches_query
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.blight_model import blight_decline_branch_is_closed
from ..rules.hybrid_resolution_payment import HYBRID_RESOLUTION_PAYMENT_KIND
from ..rules.targeted_life_decline import is_targeted_life_decline_effect
from ..rules.scheduled_life_return import is_scheduled_life_return
from .scheduled_life_return import complete_scheduled_life_return, prepare_scheduled_life_return
from .targeted_life_decline import complete_targeted_life_decline, prepare_targeted_life_decline
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
        spec.kind != "sacrifice" or not row.sacrifice_prohibited
    ) and (spec.kind != "blight" or blight_creature_can_receive(row, actor, spec.amount, query))


def _object_payment_zones(spec):
    return ('battlefield',) if spec.kind=='source_sacrifice' else spec.predicate.zones


def _source_sacrifice_payment_row(spec,actor,query):
    row=query.object(spec.source_ref,zones=('battlefield',)) if spec.source_ref is not None else None
    return row if (row is not None and not row.phased_out
                   and not row.sacrifice_prohibited and row.controller == actor) else None


def _validated(
    effect: Mapping[str, Any], actor: str, query: SemanticChoiceQuery,
    *, continuation: bool = False,
) -> tuple[FixedEffectPaymentSpec, tuple[FrozenMap, ...]]:
    fields = {'op', 'schema_version', 'player', 'payment', 'effects'}
    from ..rules.source_entry_discard_exile import FIELD, uses_entry_discard_exile, validate_entry_discard_exile_resolved

    source_discard_exile = uses_entry_discard_exile(effect)
    if source_discard_exile:
        fields.add(FIELD)
    cast_payment = effect.get('schema_version') == 6
    if cast_payment:fields.add('source_stack')
    if "declined_effects" in effect:
        fields.add("declined_effects")
    local = {'_choice_actor', '_legal_objects', '_stack_label'} if continuation else set()
    if continuation and isinstance(effect.get('payment'), Mapping) and effect['payment'].get('kind') == HYBRID_RESOLUTION_PAYMENT_KIND:
        local.add('_legal_mana_options')
    if (
        set(effect) not in (fields | local, fields | local | {'cost'})
        or effect.get('op') != 'offer_optional_mana_payment'
        or type(effect.get('schema_version')) is not int
        or effect['schema_version'] not in (2,6)
    ):
        raise SemanticChoiceError('Fixed effect payment fields are malformed')
    if cast_payment:
        from ..cast_source_sacrifice_payment import cast_source_sacrifice_spec
        try:cast_source_sacrifice_spec({key:effect[key] for key in fields},actor=actor)
        except (KeyError,TypeError,ValueError) as exc:raise SemanticChoiceError(str(exc)) from exc
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
    from ..rules.mandatory_sacrifice_reanimation import (
        MANDATORY_REANIMATION_PAYMENT_VERSIONS, mandatory_sacrifice_reanimation_parts,
    )

    if spec.schema_version in MANDATORY_REANIMATION_PAYMENT_VERSIONS:
        try:
            _, result = mandatory_sacrifice_reanimation_parts({key: effect[key] for key in fields}, resolved=True)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return spec, (FrozenMap(result),)
    if spec.schema_version == MANDATORY_ARTIFACT_SACRIFICE_PAYMENT_VERSION:
        try:
            _, result = mandatory_artifact_sacrifice_result_parts(
                {key: effect[key] for key in fields}, resolved=True,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        # The artifact is still paid if the ability's original source left.
        return spec, (FrozenMap(result),) if result["card"] is not None else ()
    if spec.schema_version == EQUIPPED_DEATH_LIFE_PRICE_VERSION:
        draw = {"op": "draw", "player": actor, "count": spec.amount, "private": True}
        expected = [draw] if spec.amount else []
        if cast_payment or "declined_effects" in effect or thaw_value(effects) != expected:
            raise SemanticChoiceError("The equipped-death payment must keep its exact private draw")
        if not spec.amount:
            return spec, ()
    if source_discard_exile:
        try:
            validate_entry_discard_exile_resolved(effect, spec, actor)
        except (KeyError, TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        return spec, tuple(FrozenMap(child) for child in effect["declined_effects"])
    if "declined_effects" in effect:
        if not cast_payment and not blight_decline_branch_is_closed(spec.kind, effects, effect["declined_effects"]):
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


def _prepare_opponent_event_payment(effect, context):
    from ..rules.opponent_event_mana_payment import resolved_opponent_event_mana_payment

    try:
        spec, declined = resolved_opponent_event_mana_payment(effect, context.actor, context.stack_controller)
    except (ValueError, TypeError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if context.actor not in context.query.active_seats:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The original payer left the game", prepend_effects=declined),
        )
    prepared = prepare_fixed_payment_cost(spec, effect, context)
    request = replace(prepared.request, public_context=FrozenMap({
        **prepared.request.public_context, "cost": dict(spec.requirements),
    }))
    return replace(prepared, request=request)


def prepare_fixed_effect_payment(
    effect: Mapping[str, Any], context: SemanticChoiceContext,
) -> SemanticChoicePreparation:
    from ..rules.entry_player_counter_payment import resolved_entry_player_payment, uses_entry_player_counter_payment

    from ..rules.opponent_event_mana_payment import uses_opponent_event_mana_payment

    if uses_opponent_event_mana_payment(effect):
        return _prepare_opponent_event_payment(effect, context)
    if uses_entry_player_counter_payment(effect):
        try:
            spec, declined = resolved_entry_player_payment(effect, context.actor)
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if context.actor not in context.query.active_seats:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The land entry player left the game"))
        prepared = prepare_fixed_payment_cost(spec, effect, context)
        if prepared.request is not None and not prepared.request.public_context["payable"]:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("No eligible creature can receive the counter", declined))
        if prepared.request is not None:
            request = replace(prepared.request,
                prompt="Put a -1/-1 counter on a creature you control or take 3 damage.")
            return replace(prepared, request=request)
        return prepared
    if is_scheduled_life_return(effect):
        return prepare_scheduled_life_return(effect, context)
    if is_targeted_life_decline_effect(effect):
        return prepare_targeted_life_decline(effect, context)
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
    return _counter_payment_intent(spec, row, effect["_choice_actor"], effect["_stack_label"])


def _counter_payment_intent(spec, row, actor, reason):
    return RemoveCountersIntent(actor=actor, object_ref=row.ref, counter_name=spec.counter_name,
        amount=spec.amount, reason=reason, source_ref=row.ref)


def prepare_mandatory_source_counter_followup(effect, context):
    from ..fixed_effect_payment import mandatory_source_counter_followup_parts

    try:
        spec, child = mandatory_source_counter_followup_parts(effect, resolved=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if effect["player"] != context.actor:
        raise SemanticChoiceError("Mandatory counter payment controller changed")
    row = _source_counter_payment_row(spec, context.query)
    if row is None or context.actor not in context.query.active_seats:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue("The original source cannot pay the required counter"),
        )
    intent = _counter_payment_intent(spec, row, context.actor, context.stack_label)
    return SemanticChoicePreparation(
        request=None, continuation_effect=FrozenMap(effect), preparation_intents=(intent,),
        auto_continue=AutoContinue("The source pays one required counter", (FrozenMap(child),)),
    )


def prepare_fixed_payment_cost(
    spec: FixedEffectPaymentSpec, effect: Mapping[str, Any],
    context: SemanticChoiceContext, *, object_filter=None,
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
                (row for row in candidates if _object_matches_payment(row, spec, actor, context.query)
                 and (object_filter is None or object_filter(row))),
                key=lambda row: (row.ref, row.logical_object_id),
            ))
        payable = len(legal) >= required
        if not payable and (spec.mandatory or spec.kind == RETURN_PAYMENT_KIND):
            reason = {
                "discard": "Mandatory discard is unavailable",
                "sacrifice": f"No eligible {spec.mandatory_sacrifice_type} to sacrifice",
                RETURN_PAYMENT_KIND: "No eligible permanent to return",
            }[spec.kind]
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
    elif spec.kind == 'resolved_fractional_life':
        from ..rules.fractional_life_payment import resolved_fractional_payment_is_payable
        payable = resolved_fractional_payment_is_payable(spec, context.query.player_life_for_payment(actor))
        choice = ScalarChoice(field_name='pay', legal_values=(True, False) if payable else (False,))
    else:
        payable = (
            (spec.amount == 0 or context.query.player_life_for_payment(actor) >= spec.amount) if spec.kind == 'life'
            else context.query.player_counter(actor, 'energy') >= spec.amount if spec.kind == 'energy'
            else context.query.cost_is_affordable(actor, spec.requirements)
        )
        choice = ScalarChoice(field_name='pay', legal_values=(True, False) if payable else (False,))
    stored = {
        **dict(effect), '_choice_actor': actor, '_stack_label': context.stack_label,
        '_legal_objects': [{'ref': row.ref, 'logical_object_id': row.logical_object_id} for row in legal],
    }
    public = {'stack': context.stack_ref, 'operation': effect['op'], 'payment_kind': spec.kind, 'payable': payable}
    prompt = "Pay the optional fixed cost or decline."
    if spec.mandatory:
        prompt = {
            "artifact": "Choose an artifact to sacrifice.",
            "creature": "Choose a creature to sacrifice.",
        }.get(spec.mandatory_sacrifice_type, "Choose a card to discard.")
    if spec.kind == 'discard':
        public['objects'] = [{'id': row.ref, 'name': row.printed_name} for row in legal]
    if spec.schema_version == EQUIPPED_DEATH_LIFE_PRICE_VERSION:
        public["life_payment"] = spec.amount
        prompt = f"Pay {spec.amount} life to draw {spec.amount} card(s), or decline."
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=prompt,
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
    from ..rules.entry_player_counter_payment import resolved_entry_player_payment, uses_entry_player_counter_payment

    from ..rules.opponent_event_mana_payment import resolved_opponent_event_mana_payment, uses_opponent_event_mana_payment

    if uses_opponent_event_mana_payment(effect):
        try:
            spec, declined = resolved_opponent_event_mana_payment(
                effect, effect.get("_choice_actor"), continuation.semantic_frame.controller, continuation=True,
            )
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        completion = complete_fixed_payment_cost(spec, effect, response, query)
        return completion if completion.intents else SemanticChoiceCompletion(prepend_effects=declined)
    if uses_entry_player_counter_payment(effect):
        try:
            spec, declined = resolved_entry_player_payment(effect, effect.get("_choice_actor"), continuation=True)
        except (ValueError, TypeError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        completion = complete_fixed_payment_cost(spec, effect, response, query)
        return completion if completion.intents else SemanticChoiceCompletion(prepend_effects=declined)
    if is_scheduled_life_return(effect):
        return complete_scheduled_life_return(continuation, response, query)
    if is_targeted_life_decline_effect(effect):
        return complete_targeted_life_decline(continuation, response, query)
    actor = effect.get('_choice_actor')
    spec, consequences = _validated(effect, actor, query, continuation=True)
    completion = complete_fixed_payment_cost(spec, effect, response, query)
    run_consequences = bool(completion.intents) != ("declined_effects" in effect)
    return SemanticChoiceCompletion(
        intents=completion.intents,
        prepend_effects=consequences if run_consequences else (),
    )


def _stack_target_change_payment_spec(effect, actor, source_ref, query):
    from ..stack_target_change_model import validate_stack_target_change_payment_effect

    try:
        validate_stack_target_change_payment_effect(effect, resolved=True)
        target = query.stack_object(effect["stack"], exclude_ref=source_ref)
        if (
            target is None or len(target.targets) != 1
            or target.controller != actor or effect["player"] != actor or actor not in query.active_seats
        ):
            raise ValueError("The targeted spell and its current paying controller are unavailable")
        return FixedEffectPaymentSpec("mana", requirements=effect["cost"])
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc


def prepare_stack_target_change_payment(effect, context):
    spec = _stack_target_change_payment_spec(effect, context.actor, context.stack_ref, context.query)
    prepared = prepare_fixed_payment_cost(spec, effect, context)
    request = replace(
        prepared.request, prompt="Pay {2} to keep the spell's target.",
        public_context=FrozenMap({**dict(prepared.request.public_context),
                                  "cost": dict(spec.requirements), "target_stack": effect["stack"]}),
    )
    return replace(prepared, request=request)


def complete_stack_target_change_payment(continuation, response, query):
    effect = continuation.effect
    local_fields = {"_choice_actor", "_stack_label", "_legal_objects"}
    if not local_fields.issubset(effect) or effect["_legal_objects"]:
        raise SemanticChoiceError("Spell target change requires its fixed mana continuation")
    source = {key: value for key, value in thaw_value(effect).items() if key not in local_fields}
    spec = _stack_target_change_payment_spec(source, effect["_choice_actor"], continuation.stack_ref, query)
    completion = complete_fixed_payment_cost(spec, effect, response, query)
    if completion.intents:
        return completion
    return SemanticChoiceCompletion(prepend_effects=tuple(FrozenMap(child) for child in source["declined_effects"]))


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
    response: Mapping[str, Any], query: SemanticChoiceQuery, *, object_filter=None,
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
                    or spec.kind!='source_sacrifice' and not _object_matches_payment(current,spec,actor,query)
                    or object_filter is not None and not object_filter(current)):
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
        elif spec.kind == 'resolved_fractional_life':
            from ..rules.fractional_life_payment import resolved_fractional_payment_is_payable
            if not resolved_fractional_payment_is_payable(spec, query.player_life_for_payment(actor)):
                raise SemanticChoiceError('Resolved fractional life payment is no longer payable')
            intent = PayLifeIntent(actor=actor, player=actor, amount=spec.amount, reason=label)
        elif spec.kind == 'life':
            if spec.amount and query.player_life_for_payment(actor) < spec.amount:
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
