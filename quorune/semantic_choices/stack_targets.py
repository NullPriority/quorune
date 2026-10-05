from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import (
    CopyStackItemIntent,
    RetargetStackItemIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
    TargetAssignmentChoice,
)


@dataclass(frozen=True, slots=True)
class StackTargetChoiceHandler:
    operation: str
    handler_id: str
    mode: str
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 707.10",
        "CR 115.7",
        "CR 608.2d",
    )
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "stack",
        "_target_stack_ref",
        "_target_schema",
        "_default_targets",
        "_choice_actor",
        "_validation_actor",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "target_stack",
        "default_targets",
        "target_schema",
    )
    mutation_path: tuple[str, ...] = (
        "authoritative target-plan query",
        "CopyStackItemIntent or RetargetStackItemIntent",
    )
    replay_fixture: str = "semantic-choice-stack-targets"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
        "tests.test_copy_objects",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        target_ref = str(effect.get("stack") or "")
        target = context.query.stack_object(
            target_ref,
            exclude_ref=context.stack_ref,
        )
        if target is None:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason="target stack object no longer exists"
                ),
            )
        validation_actor = (
            context.actor if self.mode == "copy" else target.controller
        )
        bundle = context.query.stack_target_schema(
            target.ref,
            actor=validation_actor,
        )
        if bundle is None or not target.targets:
            intents = ()
            if self.mode == "copy":
                intents = (
                    CopyStackItemIntent(
                        actor=context.actor,
                        controller=context.actor,
                        target_stack_ref=target.ref,
                        targets=tuple(
                            value for value in target.targets if value is not None
                        ),
                        target_groups=target.target_groups,
                        reason=context.stack_label,
                        target_snapshots=target.target_snapshots,
                    ),
                )
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                preparation_intents=intents,
                auto_continue=AutoContinue(
                    reason="stack object has no target choice"
                ),
            )
        authoritative = bundle.get("authoritative")
        public = bundle.get("public")
        if not isinstance(authoritative, Mapping) or not isinstance(
            public, Mapping
        ):
            raise SemanticChoiceError(
                "The target stack object has no legal target assignment"
            )
        defaults = tuple(
            str(value) for value in target.targets if value is not None
        )
        continuation_effect = FrozenMap(
            {
                **dict(effect),
                "_target_stack_ref": target.ref,
                "_target_schema": dict(authoritative),
                "_default_targets": defaults,
                "_choice_actor": context.actor,
                "_validation_actor": validation_actor,
                "_stack_label": context.stack_label,
            }
        )
        prompt = (
            "Choose targets for the copy, or keep the original targets."
            if self.mode == "copy"
            else "Choose new targets, or keep the current targets."
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt,
                choice=TargetAssignmentChoice(
                    target_schema=FrozenMap(public),
                    default_targets=defaults,
                    may_retain_each_default=self.mode == "copy",
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "target_stack": target.ref,
                        "default_targets": defaults,
                        "target_schema": dict(public),
                        "default_target_groups": target.target_groups,
                    }
                ),
            ),
            continuation_effect=continuation_effect,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        target_ref = str(effect.get("_target_stack_ref") or "")
        target = query.stack_object(target_ref)
        if target is None:
            raise SemanticChoiceError(
                "The stack object selected for the choice no longer exists"
            )
        submitted = response.get("targets")
        if submitted is None and self.mode != "copy":
            targets = tuple(
                str(value)
                for value in effect.get("_default_targets", ())
            )
            groups = target.target_groups
        else:
            schema = effect.get("_target_schema")
            if not isinstance(schema, Mapping):
                raise SemanticChoiceError(
                    "The authoritative target schema is missing"
                )
            targets, grouped = query.validate_stack_targets(
                target_ref,
                submitted,
                actor=str(effect["_validation_actor"]),
                target_schema=schema,
            )
            groups = FrozenMap(grouped)
        actor = str(effect["_choice_actor"])
        if self.mode == "copy":
            intent: Any = CopyStackItemIntent(
                actor=actor,
                controller=actor,
                target_stack_ref=target_ref,
                targets=targets,
                target_groups=groups,
                reason=str(effect["_stack_label"]),
                target_snapshots=FrozenMap(query.copied_target_snapshots(target_ref, actor=actor)),
            )
        else:
            intent = RetargetStackItemIntent(
                actor=actor,
                target_stack_ref=target_ref,
                targets=targets,
                target_groups=groups,
                source_stack_ref=continuation.stack_ref,
            )
        return SemanticChoiceCompletion(intents=(intent,))


STACK_TARGET_CHOICE_HANDLERS = (
    StackTargetChoiceHandler(
        operation="copy_stack_item",
        handler_id="choice.stack.copy.v1",
        mode="copy",
    ),
    StackTargetChoiceHandler(
        operation="retarget_stack_item",
        handler_id="choice.stack.retarget.v1",
        mode="retarget",
    ),
)
