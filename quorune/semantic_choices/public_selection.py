from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..semantic_runtime.intents import (
    CreateTokenIntent,
    LifeChangeIntent,
    MoveObjectsSimultaneouslyIntent,
    PayManaCostIntent,
    PlaceCountersIntent,
    ProliferateIntent,
    ProliferateSubject,
    RecordChoiceIntent,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    ReferenceSetChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


@dataclass(frozen=True, slots=True)
class PublicObjectChoiceHandler:
    operation: str = "choose_objects"
    handler_id: str = "choice.public.objects-then.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 608.2d",)
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "selector",
        "minimum",
        "maximum",
        "prompt",
        "then",
        "_choice_actor",
        "_legal_refs",
        "_minimum",
        "_maximum",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = (
        "MoveObjectsSimultaneouslyIntent",
        "PlaceCountersIntent",
        "LifeChangeIntent",
    )
    replay_fixture: str = "semantic-choice-public-objects"
    test_modules: tuple[str, ...] = ("tests.test_interactions_v070",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        refs = context.query.choice_candidate_refs()
        if not refs:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(reason="no eligible public objects"),
            )
        selector = dict(effect.get("selector") or {})
        minimum = min(
            int(selector.get("min", effect.get("minimum", 1))),
            len(refs),
        )
        maximum = min(
            int(
                selector.get(
                    "max",
                    effect.get("maximum", minimum),
                )
            ),
            len(refs),
        )
        if minimum > maximum:
            raise SemanticChoiceError("Object selection bounds are invalid")
        rows = tuple(
            row
            for ref in refs
            for row in [context.query.object(ref)]
            if row is not None
        )
        zones = tuple(dict.fromkeys(row.zone for row in rows))
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=str(
                    effect.get("prompt")
                    or "Choose the required public object(s)."
                ),
                choice=ObjectChoice(
                    field_name="objects",
                    legal_refs=refs,
                    zones=zones or ("battlefield",),
                    minimum=minimum,
                    maximum=maximum,
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "objects": [
                            {"id": row.ref, "name": row.printed_name}
                            for row in rows
                        ],
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_legal_refs": refs,
                    "_minimum": minimum,
                    "_maximum": maximum,
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
        effect = continuation.effect
        selected = tuple(
            str(value)
            for value in response.get("objects", response.get("cards", ()))
        )
        legal = {str(value) for value in effect.get("_legal_refs", ())}
        minimum = int(effect.get("_minimum", 0))
        maximum = int(effect.get("_maximum", len(legal)))
        if (
            len(selected) != len(set(selected))
            or not minimum <= len(selected) <= maximum
            or any(ref not in legal for ref in selected)
        ):
            raise SemanticChoiceError(
                "Object choices do not satisfy the authoritative constraints"
            )
        rows = tuple(query.object(ref) for ref in selected)
        if any(row is None for row in rows):
            raise SemanticChoiceError("A selected public object is unavailable")
        actor = str(effect["_choice_actor"])
        label = str(effect["_stack_label"])
        intents: list[Any] = []
        for raw in effect.get("then", ()):
            if not isinstance(raw, Mapping):
                raise SemanticChoiceError("Object-choice continuation is malformed")
            next_effect = dict(raw)
            next_op = str(next_effect.get("op") or "")
            reason = str(next_effect.get("reason") or label)
            if next_op in {"move_selected", "sacrifice_selected"}:
                intents.append(
                    MoveObjectsSimultaneouslyIntent(
                        actor=actor,
                        object_refs=selected,
                        expected_zones=tuple(
                            dict.fromkeys(row.zone for row in rows if row is not None)
                        ),
                        destination=(
                            "graveyard"
                            if next_op == "sacrifice_selected"
                            else str(next_effect.get("destination") or "graveyard")
                        ),
                        reason=reason,
                        transition_kind=(
                            ZoneTransitionKind.SACRIFICE
                            if next_op == "sacrifice_selected"
                            else ZoneTransitionKind.ORDINARY
                        ),
                    )
                )
            elif next_op == "add_counter_selected":
                intents.append(
                    PlaceCountersIntent(
                        actor=actor,
                        object_refs=selected,
                        counter_name=str(next_effect.get("counter") or "+1/+1"),
                        amount=int(next_effect.get("amount", 1)),
                        reason=reason,
                        source_ref=None,
                    )
                )
            elif next_op == "life_if_selected_subtype":
                subtype = str(next_effect.get("subtype") or "").casefold()
                amount = int(next_effect.get("amount", 0))
                if amount and any(
                    row is not None and subtype in row.subtypes for row in rows
                ):
                    intents.append(
                        LifeChangeIntent(
                            actor=actor,
                            player=actor,
                            amount=amount,
                            reason=reason,
                        )
                    )
            else:
                raise SemanticChoiceError(
                    f"Unsupported object-choice continuation {next_op!r}"
                )
        intents.append(
            RecordChoiceIntent(
                actor=actor,
                event_code="semantic.objects.chosen",
                message=f"{actor} chose {len(selected)} object(s) for {label}.",
                details=FrozenMap(
                    {"stack": continuation.stack_ref, "objects": selected}
                ),
            )
        )
        return SemanticChoiceCompletion(intents=tuple(intents))


@dataclass(frozen=True, slots=True)
class SpringheartLandfallChoiceHandler:
    operation: str = "springheart_landfall"
    handler_id: str = "choice.token.pay-for-attached-copy.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 702.103", "CR 707.2")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "source",
        "cost",
        "_choice_actor",
        "_source_ref",
        "_attached_ref",
        "_requirements",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "cost",
        "attached_creature",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "PayManaCostIntent",
        "CreateTokenIntent",
    )
    replay_fixture: str = "semantic-choice-springheart-landfall"
    test_modules: tuple[str, ...] = ("tests.test_exact_zimone_closure",)

    @staticmethod
    def _insect(actor: str, label: str) -> CreateTokenIntent:
        return CreateTokenIntent(
            actor=actor,
            controller=actor,
            name="Insect",
            quantity=1,
            characteristics=FrozenMap(
                {
                    "type_line": "Token Creature — Insect",
                    "colors": ["G"],
                    "power": "1",
                    "toughness": "1",
                }
            ),
            reason=label,
        )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        source_ref = str(effect.get("source") or "")
        source = context.query.object(source_ref, zones=("battlefield",))
        requirements = {
            str(key): int(value)
            for key, value in dict(
                effect.get("cost") or {"GENERIC": 1, "G": 1}
            ).items()
        }
        attached = (
            context.query.object(
                source.attached_to_ref,
                zones=("battlefield",),
            )
            if source is not None and source.attached_to_ref
            else None
        )
        can_copy = bool(
            source is not None
            and source.controller == context.actor
            and attached is not None
            and attached.controller == context.actor
            and context.query.cost_is_affordable(context.actor, requirements)
        )
        if not can_copy:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                preparation_intents=(self._insect(context.actor, context.stack_label),),
                auto_continue=AutoContinue(reason="the copy option is unavailable"),
            )
        assert source is not None and attached is not None
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Pay {1}{G} to create a token copy of the enchanted "
                    "creature, or create a 1/1 Insect."
                ),
                choice=ScalarChoice(
                    field_name="pay",
                    legal_values=(True, False),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "cost": requirements,
                        "attached_creature": attached.ref,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_source_ref": source.ref,
                    "_attached_ref": attached.ref,
                    "_requirements": requirements,
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
        effect = continuation.effect
        actor = str(effect["_choice_actor"])
        label = str(effect["_stack_label"])
        if not bool(response.get("pay", False)):
            return SemanticChoiceCompletion(intents=(self._insect(actor, label),))
        requirements = {
            str(key): int(value)
            for key, value in dict(effect.get("_requirements") or {}).items()
        }
        source = query.object(str(effect.get("_source_ref") or ""), zones=("battlefield",))
        attached = query.object(str(effect.get("_attached_ref") or ""), zones=("battlefield",))
        if (
            source is None
            or attached is None
            or source.controller != actor
            or attached.controller != actor
            or source.attached_to_ref != attached.ref
        ):
            raise SemanticChoiceError("The attachment changed before payment")
        if not query.cost_is_affordable(actor, requirements):
            raise SemanticChoiceError("The payment is no longer payable")
        return SemanticChoiceCompletion(
            intents=(
                PayManaCostIntent(
                    actor=actor,
                    player=actor,
                    requirements=FrozenMap(requirements),
                    reason=label,
                    event_code="springheart.pay",
                    message=f"{actor} paid to copy {attached.ref}.",
                    details=FrozenMap({"object": attached.ref}),
                    changed_object_ref=source.ref,
                ),
                CreateTokenIntent(
                    actor=actor,
                    controller=actor,
                    name="",
                    quantity=1,
                    copy_of=attached.ref,
                    reason=label,
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class ProliferateChoiceHandler:
    operation: str = "proliferate"
    handler_id: str = "choice.counter.proliferate.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 701.34",
        "CR 701.34a",
        "CR 122.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.proliferate",
    )
    continuation_fields: tuple[str, ...] = (
        "_choice_actor",
        "_legal_refs",
        "_stack_label",
        "_source_ref",
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt",
        "options",
        "legal_actions.choice_schema.legal_refs",
    )
    mutation_path: tuple[str, ...] = ("ProliferateIntent",)
    replay_fixture: str = "semantic-choice-proliferate"
    test_modules: tuple[str, ...] = ("tests.test_interactions_v070",)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        objects = tuple(
            row.ref
            for row in context.query.objects(zones=("battlefield",))
            if any(int(amount) > 0 for amount in row.counters.values())
        )
        players = tuple(
            seat
            for seat in context.query.active_seats
            if context.query.player_counters(seat)
        )
        refs = (*objects, *players)
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    "Choose any number of permanents and/or players with "
                    "counters to proliferate."
                ),
                choice=ReferenceSetChoice(
                    field_name="objects",
                    legal_refs=refs,
                    minimum=0,
                    maximum=len(refs),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "options": refs,
                    }
                ),
            ),
            continuation_effect=FrozenMap(
                {
                    **dict(effect),
                    "_choice_actor": context.actor,
                    "_legal_refs": refs,
                    "_stack_label": context.stack_label,
                    "_source_ref": context.source_ref,
                }
            ),
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        submitted = tuple(
            str(value)
            for value in response.get("objects", response.get("choices", ()))
        )
        legal_order = tuple(
            str(value)
            for value in continuation.effect.get("_legal_refs", ())
        )
        legal = set(legal_order)
        if len(submitted) != len(set(submitted)) or any(
            ref not in legal for ref in submitted
        ):
            raise SemanticChoiceError("Proliferate choices are not authoritative")
        selected_set = set(submitted)
        selected = tuple(ref for ref in legal_order if ref in selected_set)
        subjects: list[ProliferateSubject] = []
        for ref in selected:
            if ref in query.seats:
                names = tuple(sorted(query.player_counters(ref)))
                if not names:
                    raise SemanticChoiceError("A selected player has no counters")
                subjects.append(
                    ProliferateSubject(
                        subject_kind="player",
                        subject_id=ref,
                        ref=ref,
                        counter_names=names,
                    )
                )
            else:
                row = query.object(ref, zones=("battlefield",))
                names = (
                    tuple(
                        sorted(
                            name
                            for name, amount in row.counters.items()
                            if int(amount) > 0
                        )
                    )
                    if row is not None
                    else ()
                )
                if row is None or not names:
                    raise SemanticChoiceError("A selected permanent has no counters")
                subjects.append(
                    ProliferateSubject(
                        subject_kind="permanent",
                        subject_id=row.object_id,
                        ref=row.ref,
                        counter_names=names,
                        logical_object_id=row.logical_object_id,
                    )
                )
        actor = str(continuation.effect["_choice_actor"])
        return SemanticChoiceCompletion(
            intents=(
                ProliferateIntent(
                    actor=actor,
                    subjects=tuple(subjects),
                    reason=str(continuation.effect["_stack_label"]),
                    source_ref=(
                        str(continuation.effect["_source_ref"])
                        if continuation.effect.get("_source_ref") is not None
                        else None
                    ),
                ),
            )
        )


PUBLIC_SELECTION_CHOICE_HANDLERS = (
    PublicObjectChoiceHandler(),
    SpringheartLandfallChoiceHandler(),
    ProliferateChoiceHandler(),
)
