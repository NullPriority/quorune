"""Order only attached Roles and retain unrelated replacement-created tokens."""

from dataclasses import dataclass

from ..rules.attached_role_batch import (
    ATTACHED_ROLE_BATCH_CAPABILITY, ATTACHED_ROLE_BATCH_COMMIT, ATTACHED_ROLE_BATCH_OPERATION, PREVIEW_FIELD,
    attached_role_batch_spec, role_order_required, role_slots,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.domain_intent import DomainEffectIntent
from .model import (AutoContinue, OrderingChoice, SemanticChoiceCompletion, SemanticChoiceError,
                    SemanticChoicePreparation, SemanticChoiceRequest)


def _intent(effect, actor, order, reason):
    return DomainEffectIntent(actor=actor, operation="create_token", reason=reason,
        effect=FrozenMap({**effect, "op": "create_token", ATTACHED_ROLE_BATCH_COMMIT: order}))


@dataclass(frozen=True, slots=True)
class AttachedRoleBatchChoiceHandler:
    operation: str = ATTACHED_ROLE_BATCH_OPERATION
    handler_id: str = "choice.token.attached-role-mixed-timestamps.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("111.10", "303.4g", "608.2f", "613.7m", "614.16", "704.5z")
    capability_dependencies: tuple[str, ...] = (ATTACHED_ROLE_BATCH_CAPABILITY,)
    continuation_fields: tuple[str, ...] = ("schema_version", "player", "stack_ref", "recipients",
                                            "role", PREVIEW_FIELD, "_replacement_selections")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = ("prompt", "tokens", "legal_actions.choice_schema.legal_refs")
    mutation_path: tuple[str, ...] = ("DomainEffectIntent", "commit_attached_role_batch")
    replay_fixture: str = "attached-role-batch-with-mixed-token-replacement"
    test_modules: tuple[str, ...] = ("tests.test_not_dead_after_all",)

    def prepare(self, effect, context):
        actor = context.actor
        try:
            if actor != context.stack_controller:
                raise ValueError("Attached Role creation belongs to its original resolving controller")
            attached_role_batch_spec(effect, actor=actor, stack_ref=context.stack_ref)
            preview = tuple(context.query.token_creation_preview())
            slots = role_slots(preview)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        if not preview:
            return SemanticChoicePreparation(
                None, FrozenMap(effect), auto_continue=AutoContinue("No tokens can be created."),
            )
        frozen = FrozenMap({**effect, PREVIEW_FIELD: preview})
        if not role_order_required(slots):
            commit = _intent(frozen, actor, tuple(index for index, _ in slots), context.stack_label)
            return SemanticChoicePreparation(
                None, frozen, preparation_intents=(commit,),
                auto_continue=AutoContinue("Create the tokens in one event."),
            )
        ids = tuple(f"role:{index}" for index, _ in slots)
        return SemanticChoicePreparation(
            SemanticChoiceRequest("Order the attached Roles from earliest to latest.",
                OrderingChoice("order", ids, visibility="public"),
                FrozenMap({"stack": context.stack_ref, "tokens": [
                    {"id": f"role:{index}", "name": "Wicked Role", "recipient": ref} for index, ref in slots
                ]})), frozen,
        )

    def complete(self, continuation, response, query):
        effect = continuation.effect
        actor = continuation.semantic_frame.controller
        stable = {key: value for key, value in effect.items() if key != PREVIEW_FIELD}
        try:
            attached_role_batch_spec(stable, actor=actor, stack_ref=continuation.stack_ref)
            preview = tuple(query.token_creation_preview())
            slots = role_slots(preview)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(str(exc)) from exc
        ids = {f"role:{index}": index for index, _ in slots}
        order = response.get("order")
        if (tuple(effect[PREVIEW_FIELD]) != preview or not role_order_required(slots)
                or not isinstance(order, (list, tuple)) or any(type(item) is not str for item in order)
                or len(order) != len(ids) or set(order) != set(ids)):
            raise SemanticChoiceError("Submit a complete current order for the attached Roles")
        return SemanticChoiceCompletion(
            intents=(_intent(effect, actor, tuple(ids[item] for item in order), continuation.note),),
        )
