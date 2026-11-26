"""Resolve a committed token result and an optional attachment of its entry Aura."""

from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from ..rules.attachment_actions import (
    CHOOSE_ENTRY_AURA_RECIPIENT,
    CREATE_AND_OFFER_ENTRY_AURA,
    CREATED_ENTRY_AURA_ATTACHMENT_DEPENDENCIES,
    OFFER_ENTRY_AURA_ATTACHMENT,
    validate_created_entry_aura_attachment,
)
from .created_token_counters import (
    _attachment_offer,
    _battlefield_object,
    _create_token_intent,
    _object_identity,
)
from .model import (
    AutoContinue,
    IntentResultObjects,
    ObjectChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_FIELDS = {"op", "player", "ability_controller", "source", "source_identity", "cards"}


def _live_aura(effect, query):
    aura = _battlefield_object(query, effect["source"])
    return aura if aura is not None and _object_identity(aura) == effect["source_identity"] else None


def _attach(aura, recipient):
    return FrozenMap({
        "op": "attach", "attachment_kind": "aura", "source": aura.ref, "target": recipient,
    })


def _requires_distinct_chooser(effect, aura, offered):
    return len(offered) > 1 and aura.controller != effect["ability_controller"]


@dataclass(frozen=True, slots=True)
class CreatedEntryAuraAttachmentHandler:
    operation: str
    handler_id: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 303.4d", "CR 608.2c", "CR 608.2d", "CR 701.3b")
    capability_dependencies: tuple[str, ...] = CREATED_ENTRY_AURA_ATTACHMENT_DEPENDENCIES
    continuation_fields: tuple[str, ...] = (
        "schema_version", "controller", "aura", "token", "token_mechanics",
        "player", "ability_controller", "source", "source_identity", "cards", "_offered",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt", "legal_actions.choice_schema.legal_refs", "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = ("CreateTokenIntent", "attach")
    replay_fixture: str = "created-entry-aura-attachment"
    test_modules: tuple[str, ...] = ("tests.test_created_entry_aura_attachment",)

    def _prepare_creation(self, effect, context):
        try:
            validate_created_entry_aura_attachment(effect, resolved=True)
        except (ValueError, TypeError, KeyError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        actor = context.actor
        if actor != effect["controller"] or actor != context.stack_controller or actor not in context.query.active_seats:
            raise SemanticChoiceError("Token creation requires the resolving ability controller")
        aura = _battlefield_object(context.query, effect["aura"])
        followup = FrozenMap({
            "op": OFFER_ENTRY_AURA_ATTACHMENT,
            "player": actor,
            "ability_controller": actor,
            "source": aura.ref,
            "source_identity": _object_identity(aura),
            "cards": [],
        }) if aura is not None else None
        return SemanticChoicePreparation(
            request=None,
            continuation_effect=FrozenMap(effect),
            preparation_intents=(_create_token_intent(effect["token"], actor, context.stack_label),),
            auto_continue=AutoContinue(
                reason="Use the committed token creation results.",
                prepend_effects=(followup,) if followup is not None else (),
                objects_from_result=IntentResultObjects(0, 0) if followup is not None else None,
            ),
        )

    def prepare(self, effect, context):
        if self.operation == CREATE_AND_OFFER_ENTRY_AURA:
            return self._prepare_creation(effect, context)
        if (
            set(effect) != _FIELDS or effect["op"] != self.operation
            or effect["ability_controller"] != context.stack_controller
            or effect["player"] != context.actor or context.actor not in context.query.active_seats
        ):
            raise SemanticChoiceError("Aura attachment has a closed resolving ability and chooser")
        aura = _live_aura(effect, context.query)
        offered = _attachment_offer(effect, context.query) if aura is not None else ()
        if not offered:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="No created token can legally receive the Aura."),
            )
        if self.operation == OFFER_ENTRY_AURA_ATTACHMENT:
            if effect["player"] != effect["ability_controller"]:
                raise SemanticChoiceError("The ability controller makes the optional decision")
            if _requires_distinct_chooser(effect, aura, offered):
                prompt = "You may attach the Aura to a created token."
                choice = ScalarChoice("choice", ("apply", "decline"))
            else:
                prompt = "You may attach the Aura to one created token."
                choice = ObjectChoice(
                    "objects", tuple(row["ref"] for row in offered), ("battlefield",),
                    minimum=0, maximum=1, optional=True,
                )
        elif effect["player"] == aura.controller and effect["player"] != effect["ability_controller"]:
            prompt = "Choose one created token for the Aura."
            choice = ObjectChoice("objects", tuple(row["ref"] for row in offered), ("battlefield",))
        else:
            raise SemanticChoiceError("The Aura controller chooses among multiple recipients")
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt, choice=choice,
                public_context=FrozenMap({"stack": context.stack_ref, "source": aura.ref}),
            ),
            continuation_effect=FrozenMap({**effect, "_offered": offered}),
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        if (
            set(effect) != _FIELDS | {"_offered"} or effect["op"] != self.operation
            or effect["ability_controller"] != continuation.semantic_frame.controller
            or effect["player"] not in query.active_seats
        ):
            raise SemanticChoiceError("Aura attachment has a closed continuation")
        aura = _live_aura(effect, query)
        if aura is None or _attachment_offer(effect, query) != effect["_offered"]:
            raise SemanticChoiceError("Aura or created token incarnation changed")
        optional = self.operation == OFFER_ENTRY_AURA_ATTACHMENT
        if optional:
            if effect["player"] != effect["ability_controller"]:
                raise SemanticChoiceError("The ability controller makes the optional decision")
            if _requires_distinct_chooser(effect, aura, effect["_offered"]):
                if response.get("choice") == "decline":
                    return SemanticChoiceCompletion()
                if response.get("choice") != "apply":
                    raise SemanticChoiceError("Choose whether to attach the Aura")
                followup = {key: effect[key] for key in _FIELDS}
                followup.update(op=CHOOSE_ENTRY_AURA_RECIPIENT, player=aura.controller)
                return SemanticChoiceCompletion(prepend_effects=(FrozenMap(followup),))
        elif effect["player"] != aura.controller or effect["player"] == effect["ability_controller"]:
            raise SemanticChoiceError("The Aura controller chooses among multiple recipients")
        selected = response.get("objects")
        legal = {row["ref"] for row in effect["_offered"]}
        if not isinstance(selected, (list, tuple)) or len(selected) > 1 or not optional and len(selected) != 1:
            raise SemanticChoiceError("Choose from the offered created tokens")
        if not selected:
            return SemanticChoiceCompletion()
        ref, = selected
        if type(ref) is not str or ref not in legal:
            raise SemanticChoiceError("Choose an offered created token")
        return SemanticChoiceCompletion(prepend_effects=(_attach(aura, ref),))


CREATED_ENTRY_AURA_ATTACHMENT_HANDLERS = (
    CreatedEntryAuraAttachmentHandler(CREATE_AND_OFFER_ENTRY_AURA, "choice.token.create-then-offer-entry-aura.v1"),
    CreatedEntryAuraAttachmentHandler(OFFER_ENTRY_AURA_ATTACHMENT, "choice.token.offer-entry-aura-attachment.v1"),
    CreatedEntryAuraAttachmentHandler(CHOOSE_ENTRY_AURA_RECIPIENT, "choice.token.choose-entry-aura-recipient.v1"),
)
