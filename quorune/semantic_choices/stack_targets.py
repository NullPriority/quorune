from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..rules.cast_creature_set_spell_copy import CAST_CREATURE_SET_COPY_FIELD
from ..rules.cast_source_spell_copy import SOURCE_CAST_COPY_FIELD
from ..rules.source_directed_redirect_model import is_source_directed_redirect
from ..stack_spell_copy_model import FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD, PERMANENT_SPELL_COPY_FIELD, PermanentSpellCopySpec
from ..stack_target_change_model import is_mandatory_single_stack_target_change, is_optional_stack_target_change
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
        "copy_source",
        "retain_targets_only",
        FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD,
        "_redirect_slots",
        SOURCE_CAST_COPY_FIELD,
        CAST_CREATURE_SET_COPY_FIELD,
        "player",
        "_copy_receiver_ids",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "target_stack",
        "default_targets",
        "target_schema",
        "legal_actions.choice_schema.legal_values",
        "legal_actions.choice_schema.legal_refs",
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
        if CAST_CREATURE_SET_COPY_FIELD in effect:
            from .cast_creature_set_copy import prepare_cast_creature_set_copy
            return prepare_cast_creature_set_copy(effect, context)
        if SOURCE_CAST_COPY_FIELD in effect:
            from .source_cast_copy import prepare_source_cast_copy
            return prepare_source_cast_copy(effect, context)
        if is_source_directed_redirect(effect):
            from .source_directed_redirect import prepare_source_directed_redirect
            return prepare_source_directed_redirect(effect, context)
        permanent_copy = None
        if PERMANENT_SPELL_COPY_FIELD in effect:
            try:
                permanent_copy = PermanentSpellCopySpec.from_dict(effect[PERMANENT_SPELL_COPY_FIELD])
            except (TypeError, ValueError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            if self.mode != "copy" or effect.get("retain_targets_only") is not True:
                raise SemanticChoiceError("Permanent spell copy must retain the original choices")
        copies = effect.get(FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD, 1)
        if FIXED_PERMANENT_SPELL_COPY_COUNT_FIELD in effect and (
            self.mode != "copy" or type(copies) is not int or copies != 3
            or effect.get("retain_targets_only") is not True or permanent_copy is not None or "copy_source" in effect
        ):
            raise SemanticChoiceError("Three copies require the complete retained-target instruction")
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
        if permanent_copy is not None:
            if permanent_copy.kind == "artifact" and target.copy_source is None:
                raise SemanticChoiceError("Artifact spell copy requires its retained cast source")
            if permanent_copy.kind == "nonlegendary" and (
                target.controller != context.actor or target.copy_source is not None
            ):
                raise SemanticChoiceError("Nonlegendary spell copy requires its controlled target")
        if copies == 3 and target.controller != context.actor:
            raise SemanticChoiceError("Three copies require the current controlled spell target")
        validation_actor = (
            context.actor if self.mode == "copy" else target.controller
        )
        bundle = context.query.stack_target_schema(
            target.ref,
            actor=validation_actor,
        )
        if bundle is None or not target.targets or effect.get('retain_targets_only') is True:
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
                        copy_source=target.copy_source,
                        permanent_copy=permanent_copy,
                    ),
                ) * copies
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
                **({'copy_source':target.copy_source} if target.copy_source is not None else {}),
                "_target_stack_ref": target.ref,
                "_target_schema": dict(authoritative),
                "_default_targets": defaults,
                "_choice_actor": context.actor,
                "_validation_actor": validation_actor,
                "_stack_label": context.stack_label,
            }
        )
        mandatory = is_mandatory_single_stack_target_change(effect)
        if mandatory:
            prompt = "Choose a different legal target."
            offered_defaults = ()
        else:
            prompt = ("Choose targets for the copy, or keep the original targets."
                      if self.mode == "copy" else "Choose new targets, or keep the current targets.")
            offered_defaults = defaults
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt,
                choice=TargetAssignmentChoice(
                    target_schema=FrozenMap(public),
                    default_targets=offered_defaults,
                    optional=not mandatory,
                    may_retain_each_default=self.mode == "copy" or is_optional_stack_target_change(effect),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "target_stack": target.ref,
                        "default_targets": offered_defaults,
                        "target_schema": dict(public),
                        "default_target_groups": {} if mandatory else target.target_groups,
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
        if CAST_CREATURE_SET_COPY_FIELD in effect:
            from .cast_creature_set_copy import complete_cast_creature_set_copy
            return complete_cast_creature_set_copy(effect, response, query)
        if SOURCE_CAST_COPY_FIELD in effect:
            from .source_cast_copy import complete_source_cast_copy
            return complete_source_cast_copy(effect, response, query)
        if is_source_directed_redirect(effect):
            from .source_directed_redirect import complete_source_directed_redirect
            return complete_source_directed_redirect(continuation, response, query)
        target_ref = str(effect.get("_target_stack_ref") or "")
        target = query.stack_object(target_ref)
        if target is None:
            raise SemanticChoiceError(
                "The stack object selected for the choice no longer exists"
            )
        submitted = response.get("targets")
        mandatory = is_mandatory_single_stack_target_change(effect)
        optional_reselection = is_optional_stack_target_change(effect)
        if submitted is None and optional_reselection:
            return SemanticChoiceCompletion()
        if submitted is None and mandatory:
            raise SemanticChoiceError("Choose one different legal target")
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
                copy_source=target.copy_source,
            )
        else:
            snapshots = None
            if optional_reselection:
                snapshots = FrozenMap(query.copied_target_snapshots(target_ref, actor=str(effect["_validation_actor"])))
                if targets == target.targets and groups == target.target_groups and snapshots == target.target_snapshots:
                    return SemanticChoiceCompletion()
            intent = RetargetStackItemIntent(
                actor=actor,
                target_stack_ref=target_ref,
                targets=targets,
                target_groups=groups,
                source_stack_ref=continuation.stack_ref,
                collect_single_target_ward=mandatory,
                target_snapshots=snapshots,
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
