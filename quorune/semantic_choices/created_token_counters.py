from __future__ import annotations

from dataclasses import dataclass
from ..created_token_counter_model import (
    ANNOUNCED_X_CREATE_AND_WIPE, CREATED_TOKEN_WIPE_CAPABILITY, CREATE_THEN_COUNTER,
    COUNTER_CREATED, DEPENDENCIES, DESTROY_EXCEPT_CREATED,
    validate_announced_x_token_wipe, validate_created_token_counter,
)
from ..affected_permanents import AffectedPermanentSetSpec
from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..rules.attachment_actions import (
    ATTACH_CREATED_TOKEN,
    CREATE_AND_ATTACH,
    CREATED_TOKEN_ATTACHMENT_DEPENDENCIES,
    validate_created_token_attachment,
)
from ..rules.death_battlefield_return import ATTACH_RETURNED_WITH_EQUIPMENT, DEATH_EQUIPMENT_RETURN_DEPENDENCIES
from ..semantic_runtime.intents import CreateTokenIntent, DestroyPermanentSetIntent, LifeChangeIntent, PlaceCountersIntent
from .model import (
    AutoContinue,
    IntentResultObjects,
    ObjectChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _create_token_intent(token, actor, label, *, quantity=1):
    return CreateTokenIntent(
        actor=actor, controller=actor, name=token["name"], quantity=quantity,
        characteristics=FrozenMap(token["characteristics"]), tapped=token.get("tapped", False),
        reason=label,
    )


@dataclass(frozen=True,slots=True)
class CreatedTokenCounterHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str,...] = ('111.3','122.1','608.2c','704.4')
    capability_dependencies: tuple[str,...] = DEPENDENCIES
    continuation_fields: tuple[str,...] = ('controller','source','token','token_mechanics','cards','amount')
    private_data: tuple[str,...] = ()
    projected_fields: tuple[str,...] = ()
    mutation_path: tuple[str,...] = ('CreateTokenIntent','PlaceCountersIntent')
    replay_fixture: str = 'created-token-counter-followup'
    test_modules: tuple[str,...] = ('tests.test_created_token_counters',)

    def prepare(self,effect,context):
        try:validate_created_token_counter(effect,resolved=self.operation==COUNTER_CREATED)
        except (ValueError,TypeError,KeyError) as exc:raise SemanticChoiceError(str(exc)) from exc
        actor=context.actor
        if effect['controller']!=actor or actor not in context.query.active_seats or effect['source']!=context.source_ref:
            raise SemanticChoiceError('Created-token counter parent, controller or source changed')
        if self.operation==CREATE_THEN_COUNTER:
            token=effect['token']
            intent = _create_token_intent(token, actor, context.stack_label)
            follow=FrozenMap({'op':COUNTER_CREATED,'schema_version':1,'controller':actor,
                'source':effect['source'],'amount':effect['amount'],'cards':[]})
            return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=(intent,),
                auto_continue=AutoContinue(reason='Apply the following instruction to actual creation results.',
                    prepend_effects=(follow,),objects_from_result=IntentResultObjects(0,0)))
        refs=tuple(ref for ref in effect['cards'] if context.query.object(ref,zones=('battlefield',)) is not None)
        intents=(PlaceCountersIntent(actor=actor,object_refs=refs,counter_name='+1/+1',amount=effect['amount'],
            reason=context.stack_label,source_ref=effect['source']),) if refs and effect['amount'] else ()
        return SemanticChoicePreparation(request=None,continuation_effect=FrozenMap(effect),preparation_intents=intents,
            auto_continue=AutoContinue(reason='Created-object counter instruction completed.'))

    def complete(self,continuation,response,query):
        raise SemanticChoiceError('Created-token counter instructions do not accept a player-selected recipient')


_ATTACH_FIELDS = {"op", "player", "source", "source_identity", "cards"}


def _object_identity(row):
    return FrozenMap({"ref": row.ref, "object_id": row.object_id, "incarnation": row.logical_object_id})


def _battlefield_object(query, ref):
    row = query.object(ref, zones=("battlefield",)) if type(ref) is str and ref else None
    return row if row is not None and not row.phased_out else None


def _attachment_offer(effect, query, *, token_result):
    refs = effect["cards"]
    if not isinstance(refs, (list, tuple)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise SemanticChoiceError("Created attachment subjects must be distinct references")
    eligible = query.choice_candidate_refs()
    if len(eligible) != len(set(eligible)) or not set(eligible) <= set(refs):
        raise SemanticChoiceError("Attachment choice includes a non-created object")
    rows = tuple(_battlefield_object(query, ref) for ref in eligible)
    if any(row is None or row.token != token_result for row in rows):
        raise SemanticChoiceError("Created attachment subject is unavailable")
    return tuple(_object_identity(row) for row in rows)


def _attach_effect(source, target):
    return FrozenMap({"op": "attach", "attachment_kind": "equipment", "source": source, "target": target})


@dataclass(frozen=True, slots=True)
class CreatedTokenAttachmentHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 301.5c", "CR 608.2c", "CR 701.3b")
    capability_dependencies: tuple[str, ...] = CREATED_TOKEN_ATTACHMENT_DEPENDENCIES
    continuation_fields: tuple[str, ...] = (
        "schema_version", "controller", "source", "token", "token_mechanics",
        "player", "source_identity", "cards", "_offered",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("CreateTokenIntent", "attach")
    replay_fixture: str = "created-token-equipment-choice"
    test_modules: tuple[str, ...] = ("tests.test_fixed_attachment_actions",)
    token_result: bool = True

    def _prepare_creation(self, effect, context):
        try:
            validate_created_token_attachment(effect, resolved=True)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        actor = context.actor
        source = _battlefield_object(context.query, context.source_ref)
        if source is not None and (
            source.object_id != context.source_object_id
            or source.logical_object_id != context.source_logical_object_id
        ):
            source = None
        if effect["controller"] != actor or actor not in context.query.active_seats or effect["source"] != (
            source.ref if source is not None else None
        ):
            raise SemanticChoiceError("Attached-token creation controller or source changed")
        follow = FrozenMap({
            "op": ATTACH_CREATED_TOKEN, "player": "$source.controller",
            "source": source.ref, "source_identity": _object_identity(source), "cards": [],
        }) if source is not None else None
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(_create_token_intent(effect["token"], actor, context.stack_label),),
            auto_continue=AutoContinue(
                reason="Apply the attachment to actual creation results.",
                prepend_effects=(follow,) if follow is not None else (),
                objects_from_result=IntentResultObjects(0, 0) if follow is not None else None,
            ),
        )

    def prepare(self, effect, context):
        if self.operation == CREATE_AND_ATTACH:
            return self._prepare_creation(effect, context)
        parent_identity = {
            "ref": context.source_ref, "object_id": context.source_object_id,
            "incarnation": context.source_logical_object_id,
        }
        if (
            set(effect) != _ATTACH_FIELDS or effect["op"] != self.operation
            or type(effect["player"]) is not str or effect["player"] != context.actor
            or effect["source"] != context.source_ref or effect["source_identity"] != parent_identity
        ):
            raise SemanticChoiceError("Created attachment instruction has a closed source binding")
        source = _battlefield_object(context.query, effect["source"])
        if source is None or _object_identity(source) != effect["source_identity"]:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="The Equipment is unavailable."),
            )
        if source.controller != context.actor or context.actor not in context.query.active_seats:
            raise SemanticChoiceError("Equipment controller changed before the attachment choice")
        offered = _attachment_offer(effect, context.query, token_result=self.token_result)
        if len(offered) < 2:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason="Resolve the attachment with at most one legal creature.",
                    prepend_effects=(_attach_effect(source.ref, offered[0]["ref"]),) if offered else (),
                ),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose a created creature to equip." if self.token_result else "Choose a returned creature to equip.",
                choice=ObjectChoice(
                    field_name="objects", legal_refs=tuple(row["ref"] for row in offered),
                    zones=("battlefield",),
                ),
            ),
            continuation_effect=FrozenMap({**dict(effect), "_offered": offered}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if (self.operation not in {ATTACH_CREATED_TOKEN, ATTACH_RETURNED_WITH_EQUIPMENT}
                or effect.get("op") != self.operation or set(effect) != _ATTACH_FIELDS | {"_offered"}):
            raise SemanticChoiceError("Created attachment choice has a closed continuation")
        source = _battlefield_object(query, effect["source"])
        parent = query.resolution_source
        if (
            source is None or parent is None
            or source.object_id != parent.object_id or source.logical_object_id != parent.logical_object_id
            or _object_identity(source) != effect["source_identity"]
            or source.controller != effect["player"] or source.controller not in query.active_seats
        ):
            raise SemanticChoiceError("Equipment identity or controller changed")
        offered = _attachment_offer(effect, query, token_result=self.token_result)
        if offered != effect["_offered"] or len(offered) < 2:
            raise SemanticChoiceError("Created attachment choices changed")
        selected = response.get("objects", response.get("cards"))
        if (
            not isinstance(selected, (list, tuple)) or len(selected) != 1
            or type(selected[0]) is not str or selected[0] not in {row["ref"] for row in offered}
        ):
            raise SemanticChoiceError("Choose one offered created creature")
        return SemanticChoiceCompletion(prepend_effects=(_attach_effect(source.ref, selected[0]),))


CREATED_TOKEN_ATTACHMENT_HANDLERS = (
    CreatedTokenAttachmentHandler(CREATE_AND_ATTACH, "choice.token.create-then-attach.v1"),
    CreatedTokenAttachmentHandler(ATTACH_CREATED_TOKEN, "choice.token.attach-created-results.v1"),
    CreatedTokenAttachmentHandler(
        ATTACH_RETURNED_WITH_EQUIPMENT, "choice.attachment.death-return-results.v1",
        capability_dependencies=DEATH_EQUIPMENT_RETURN_DEPENDENCIES,
        replay_fixture="death-returned-equipment-choice",
        test_modules=("tests.test_event_card_return",),
        token_result=False,
    ),
)


@dataclass(frozen=True, slots=True)
class AnnouncedXCreatedTokenWipeHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ('107.3a', '111.3', '608.2c', '701.8a', '704.4')
    capability_dependencies: tuple[str, ...] = (CREATED_TOKEN_WIPE_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        'schema_version', 'controller', 'source', 'amount', 'minimum', 'token', 'token_mechanics', 'cards', 'stack',
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ()
    mutation_path: tuple[str, ...] = ('LifeChangeIntent', 'CreateTokenIntent', 'DestroyPermanentSetIntent')
    replay_fixture: str = 'announced-x-created-token-destruction'
    test_modules: tuple[str, ...] = ('tests.test_created_token_counters',)

    def prepare(self, effect, context):
        actor = context.actor
        if (effect.get('op') != self.operation or effect.get('controller') != actor
                or actor != context.stack_controller or actor not in context.query.active_seats
                or effect.get('source') != context.source_ref):
            raise SemanticChoiceError('Creation and destruction require the same resolving controller and source')
        if self.operation == DESTROY_EXCEPT_CREATED:
            return self._destruction(effect, context)
        try:
            validate_announced_x_token_wipe(effect, resolved=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        x = effect['amount']
        if x == 0:
            return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason='The announced X is zero.'))
        continuation = AutoContinue(reason='The announced X is below the destruction threshold.')
        if x >= effect['minimum']:
            following = FrozenMap({'op': DESTROY_EXCEPT_CREATED, 'schema_version': 1, 'controller': actor,
                'source': context.source_ref, 'stack': context.stack_ref, 'cards': []})
            continuation = AutoContinue(reason='Retain the actual creations for simultaneous destruction.',
                prepend_effects=(following,), objects_from_result=IntentResultObjects(1, 0))
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(LifeChangeIntent(actor, actor, x, context.stack_label, context.source_ref),
                _create_token_intent(effect['token'], actor, context.stack_label, quantity=x)), auto_continue=continuation)

    def _destruction(self, effect, context):
        fields = {'op', 'schema_version', 'controller', 'source', 'stack', 'cards'}
        if (set(effect) != fields or type(effect['schema_version']) is not int or effect['schema_version'] != 1
                or context.stack_ref != effect['stack']):
            raise SemanticChoiceError('Destruction requires the original token result continuation')
        refs = effect['cards']
        if (not isinstance(refs, (list, tuple)) or any(type(ref) is not str or not ref for ref in refs)
                or len(set(refs)) != len(refs)):
            raise SemanticChoiceError('The committed creation result must contain distinct token references')
        identities = []
        for ref in refs:
            card = context.query.object(ref, zones=('battlefield',))
            if card is not None:
                if not card.token:
                    raise SemanticChoiceError('A committed token result cannot name a nontoken permanent')
                identities.append(ContinuousObjectIdentity(card.object_id, card.logical_object_id))
        creatures = AffectedPermanentSetSpec(query=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)))
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(effect),
            preparation_intents=(DestroyPermanentSetIntent(actor=context.actor, spec=creatures, reason=context.stack_label,
                source_ref=context.source_ref, excluded_created_tokens=tuple(identities)),),
            auto_continue=AutoContinue(reason='Destroy all other creatures together.'))

    def complete(self, continuation, response, query):
        raise SemanticChoiceError('The creating spell and destruction require no player-selected targets')


CREATED_TOKEN_COUNTER_HANDLERS = (
    CreatedTokenCounterHandler(CREATE_THEN_COUNTER, 'choice.token.create-then-counters.v1'),
    CreatedTokenCounterHandler(COUNTER_CREATED, 'choice.token.counter-created-results.v1'),
    AnnouncedXCreatedTokenWipeHandler(ANNOUNCED_X_CREATE_AND_WIPE, 'choice.token.announced-x-create-and-wipe.v1'),
    AnnouncedXCreatedTokenWipeHandler(DESTROY_EXCEPT_CREATED, 'choice.token.destroy-except-created.v1'),
)
