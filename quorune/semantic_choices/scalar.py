from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..creature_subtypes import CREATURE_SUBTYPES, canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION, CHOSEN_SUBTYPE_SETTING_VERSION, decode_temporary_creature_type_choice,
)
from ..semantic_runtime.intents import (
    AddManaIntent,
    RecordChoiceIntent,
    SetCardDesignationIntent,
)
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


def _effect_with_context(
    effect: Mapping[str, Any],
    context: SemanticChoiceContext,
) -> FrozenMap:
    return FrozenMap(
        {
            **dict(effect),
            "_choice_actor": context.actor,
            "_source_ref": context.source_ref,
        }
    )


@dataclass(frozen=True, slots=True)
class ChooseManaHandler:
    operation: str = "choose_mana"
    handler_id: str = "choice.scalar.mana.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 106.3", "CR 608.2d")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "colors",
        "amount",
        "player",
        "_choice_actor",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "options",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "AddManaIntent",
        "CommanderEngine.apply_mana_intent",
    )
    replay_fixture: str = "semantic-choice-scalar-mana"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
    )

    @staticmethod
    def _colors(effect: Mapping[str, Any]) -> tuple[str, ...]:
        colors = tuple(
            str(value).upper()
            for value in effect.get("colors", tuple("WUBRGC"))
            if isinstance(value, str)
            and len(value) == 1
            and value.upper() in "WUBRGC"
        )
        if not colors:
            raise SemanticChoiceError(
                "Semantic mana choice requires at least one legal color"
            )
        return colors

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if {'schema_version','selection','source'}&set(effect):
            from .resolution_mana import prepare_resolution_mana
            return prepare_resolution_mana(effect,context)
        colors = self._colors(effect)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose a mana color.",
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=colors,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "options": list(colors),
                    }
                ),
            ),
            continuation_effect=_effect_with_context(effect, context),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        if {'schema_version','selection','source'}&set(effect):
            from .resolution_mana import complete_resolution_mana
            return complete_resolution_mana(continuation,response,query)
        choice = str(
            response.get("choice")
            or response.get("color")
            or response.get("mana")
            or ""
        ).upper()
        colors = self._colors(effect)
        if choice not in colors:
            raise SemanticChoiceError("Choose one of " + ", ".join(colors))
        amount = int(effect.get("amount", 1))
        if amount < 0:
            raise SemanticChoiceError("Chosen mana amount cannot be negative")
        actor = str(effect["_choice_actor"])
        player = str(effect.get("player") or actor)
        return SemanticChoiceCompletion(
            intents=(
                AddManaIntent(
                    player=player,
                    color=choice,
                    amount=amount,
                    actor=actor,
                    reason="semantic mana choice",
                    source_ref=continuation.stack_ref,
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ChooseCardNameHandler:
    operation: str = "choose_card_name"
    handler_id: str = "choice.scalar.card-name.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 201.3", "CR 608.2d")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = ("_choice_actor", "_source_ref")
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.type",
    )
    mutation_path: tuple[str, ...] = (
        "SetCardDesignationIntent",
        "CommanderEngine.set_card_designation_intent",
    )
    replay_fixture: str = "semantic-choice-scalar-card-name"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if context.source_ref is None:
            raise SemanticChoiceError(
                "The naming effect no longer has a source object"
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose a Magic card name.",
                choice=ScalarChoice(
                    field_name="card_name",
                    value_type="card_name",
                    nonempty=True,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                    }
                ),
            ),
            continuation_effect=_effect_with_context(effect, context),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        raw_name = str(response.get("card_name") or "").strip()
        if not raw_name:
            raise SemanticChoiceError("A card name is required")
        chosen = query.canonical_card_name(raw_name)
        if chosen is None:
            raise SemanticChoiceError(
                f"{raw_name!r} is not a recognized Magic card name"
            )
        return SemanticChoiceCompletion(
            intents=(
                SetCardDesignationIntent(
                    object_ref=str(continuation.effect["_source_ref"]),
                    designation="chosen_name",
                    value=chosen,
                    actor=str(continuation.effect["_choice_actor"]),
                    reason="card name chosen",
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ChooseCreatureTypeHandler:
    operation: str = "choose_creature_type"
    handler_id: str = "choice.scalar.creature-type.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 205.3m", "CR 608.2d")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "apply_as_subtype",
        "schema_version",
        "excluded",
        "card",
        "predicate",
        "_choice_actor",
        "_recipient",
        "_source_ref",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "legal_actions.choice_schema.type",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "SetCardDesignationIntent",
        "CommanderEngine.set_card_designation_intent",
        "RecordChoiceIntent",
        "apply_fixed_resolution_characteristics",
    )
    replay_fixture: str = "semantic-choice-scalar-creature-type"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
        "tests.test_fixed_source_characteristic_effects",
    )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if "schema_version" in effect:
            return _prepare_temporary_creature_type(effect, context)
        if type(effect.get("apply_as_subtype", False)) is not bool:
            raise SemanticChoiceError(
                "Creature-type subtype application must be boolean"
            )
        if context.source_ref is None:
            raise SemanticChoiceError(
                "The creature-type choice has no source object"
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt="Choose a creature type.",
                choice=ScalarChoice(
                    field_name="creature_type",
                    value_type="creature_type",
                    nonempty=True,
                    max_length=48,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                    }
                ),
            ),
            continuation_effect=_effect_with_context(effect, context),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        if "schema_version" in continuation.effect:
            return _complete_temporary_creature_type(continuation, response, query)
        creature_type = str(
            response.get("creature_type") or response.get("choice") or ""
        ).strip()
        if (
            not creature_type
            or len(creature_type) > 48
            or re.fullmatch(r"[A-Za-z][A-Za-z '\-]*", creature_type) is None
        ):
            raise SemanticChoiceError("Choose a valid nonempty creature type")
        return SemanticChoiceCompletion(
            intents=(
                SetCardDesignationIntent(
                    object_ref=str(continuation.effect["_source_ref"]),
                    designation="chosen_creature_type",
                    value=creature_type.title(),
                    actor=str(continuation.effect["_choice_actor"]),
                    reason="creature type chosen",
                    apply_as_subtype=bool(
                        continuation.effect.get("apply_as_subtype", False)
                    ),
                ),
            )
        )


def _temporary_type_selection(effect, actor):
    try:
        subject, excluded = decode_temporary_creature_type_choice(effect, resolved=True)
    except (TypeError, ValueError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if isinstance(subject, ObjectQuerySpec) and subject.controller not in (None, actor):
        raise SemanticChoiceError("The creature set no longer belongs to the resolving controller")
    return subject, excluded


def _creature_type_recipient(query, ref):
    card = query.object(ref, zones=("battlefield",))
    if card is None or card.phased_out:
        return None
    return ContinuousObjectIdentity(card.object_id, card.logical_object_id)


def _prepare_temporary_creature_type(effect, context):
    subject, excluded = _temporary_type_selection(effect, context.actor)
    identity = _creature_type_recipient(context.query, subject) if isinstance(subject, str) else None
    if isinstance(subject, str) and identity is None:
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(effect),
            auto_continue=AutoContinue(reason="The original creature type recipient is unavailable"),
        )
    options = tuple(sorted(value.title() for value in CREATURE_SUBTYPES if value != excluded))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt="Choose a creature type.",
            choice=ScalarChoice(field_name="creature_type", value_type="creature_type", legal_values=options,
                                nonempty=True, max_length=48),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": "choose_creature_type"}),
        ),
        continuation_effect=FrozenMap({**effect, "_choice_actor": context.actor,
                                       "_recipient": identity.to_dict() if identity else None}),
    )


def _complete_temporary_creature_type(continuation, response, query):
    effect = dict(continuation.effect)
    if not {"_choice_actor", "_recipient"} <= set(effect):
        raise SemanticChoiceError("A temporary creature type choice lost its continuation facts")
    actor = effect.pop("_choice_actor")
    recipient = effect.pop("_recipient")
    item = query.stack_object(continuation.stack_ref)
    if item is None or actor != item.controller or actor not in query.active_seats:
        raise SemanticChoiceError("The creature type chooser no longer controls the resolving item")
    subject, excluded = _temporary_type_selection(effect, actor)
    fields = {"action", "action_id", "reason", "choice_schema", "label", "creature_type", "choice"}
    choices = [response[key] for key in ("creature_type", "choice") if key in response]
    subtype = canonical_creature_subtype(choices[0]) if len(choices) == 1 else None
    if set(response) - fields or subtype is None or subtype == excluded:
        raise SemanticChoiceError("Choose one legal creature type")
    chosen = RecordChoiceIntent(actor=actor, event_code="semantic.option.chosen",
        message=f"{actor} chose {subtype.title()}.", details=FrozenMap({"stack": continuation.stack_ref, "creature_type": subtype}))
    if isinstance(subject, str):
        try:
            original = ContinuousObjectIdentity.from_dict(recipient)
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError("The original creature type recipient is malformed") from exc
        if _creature_type_recipient(query, subject) != original:
            return SemanticChoiceCompletion(intents=(chosen,))
        selection = {"card": subject}
    else:
        if recipient is not None:
            raise SemanticChoiceError("A creature set cannot retain an individual recipient")
        selection = {"predicate": subject.to_dict()}
    result = {"op": CHARACTERISTIC_OPERATION, "schema_version": CHOSEN_SUBTYPE_SETTING_VERSION,
              "characteristics": {"subtype": subtype}, **selection}
    return SemanticChoiceCompletion(intents=(chosen,), prepend_effects=(FrozenMap(result),))


@dataclass(frozen=True, slots=True)
class ChooseOptionHandler:
    operation: str = "choose_option"
    handler_id: str = "choice.scalar.option.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 608.2d",)
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "options",
        "then_by_choice",
        "_legal_values",
        "_choice_actor",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "options",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "RecordChoiceIntent",
        "semantic resolution continuation",
    )
    replay_fixture: str = "semantic-choice-scalar-option"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
    )

    @staticmethod
    def _options(effect: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
        options = tuple(
            {
                "id": str(
                    option.get("id")
                    if isinstance(option, Mapping)
                    else option
                ),
                "label": str(
                    option.get("label")
                    if isinstance(option, Mapping)
                    else option
                ),
            }
            for option in effect.get("options") or ()
        )
        if not options or any(not option["id"] for option in options):
            raise SemanticChoiceError(
                "Semantic option choice requires nonempty options"
            )
        if len({option["id"] for option in options}) != len(options):
            raise SemanticChoiceError(
                "Semantic option identifiers must be unique"
            )
        return options

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if "venture" in effect:
            from .venture import prepare_venture_choice

            return prepare_venture_choice(effect, context)
        if "attacking_card_entry" in effect:
            from .attacking_card_entry import prepare_attacking_card_entry_choice

            prepared = prepare_attacking_card_entry_choice(effect, context)
            if isinstance(prepared, SemanticChoicePreparation):
                return prepared
            effect = prepared
        options = self._options(effect)
        legal_values = tuple(option["id"] for option in options)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=str(effect.get("prompt") or "Choose one option."),
                choice=ScalarChoice(
                    field_name="choice",
                    legal_values=legal_values,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "options": options,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(_effect_with_context(effect, context)),
                    "_legal_values": legal_values,
                    "_stack_label": context.stack_label,
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        if "venture" in continuation.effect:
            from .venture import complete_venture_choice

            return complete_venture_choice(continuation, response, query)
        choice = str(
            response.get("choice") or response.get("option") or ""
        )
        legal_values = {
            str(value)
            for value in continuation.effect.get("_legal_values", ())
        }
        if choice not in legal_values:
            raise SemanticChoiceError(
                "Choose one of the authoritative option values"
            )
        by_choice = continuation.effect.get("then_by_choice", FrozenMap())
        selected_effects = by_choice.get(choice, ())
        if not isinstance(selected_effects, tuple) or any(
            not isinstance(value, Mapping) for value in selected_effects
        ):
            raise SemanticChoiceError(
                "Chosen option effects must be a list of mappings"
            )
        actor = str(continuation.effect["_choice_actor"])
        return SemanticChoiceCompletion(
            intents=(
                RecordChoiceIntent(
                    actor=actor,
                    event_code="semantic.option.chosen",
                    message=(
                        f"{actor} chose {choice} for "
                        f"{continuation.effect['_stack_label']}."
                    ),
                    details=FrozenMap(
                        {
                            "stack": continuation.stack_ref,
                            "choice": choice,
                        }
                    ),
                ),
            ),
            prepend_effects=tuple(FrozenMap(value) for value in selected_effects),
        )


SCALAR_CHOICE_HANDLERS = (
    ChooseManaHandler(),
    ChooseCardNameHandler(),
    ChooseCreatureTypeHandler(),
    ChooseOptionHandler(),
)
