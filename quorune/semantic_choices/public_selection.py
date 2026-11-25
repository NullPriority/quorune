from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap
from ..counter_placement_targets import (
    CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD, CONTROLLED_CREATURE_COUNTER_SNAPSHOTS,
    OPPONENT_CREATURE_COUNTER_CHOICE_VERSION,
    controlled_creature_counter_choice_dependencies,
)
from ..rules.controlled_return import CONTROLLED_RETURN_FIELD
from ..rules.distinct_sacrifices import DISTINCT_SACRIFICE_FIELD
from ..rules.opponent_greatest_power_destruction import GREATEST_POWER_DESTRUCTION_FIELD
from ..rules.opponent_greatest_power_exile import GREATEST_POWER_EXILE_FIELD
from .opponent_greatest_power_exile import (
    GREATEST_POWER_EXILE_CHOICES, complete_greatest_power_exile, prepare_greatest_power_exile,
)
from .opponent_greatest_power_destruction import (
    GREATEST_POWER_DESTRUCTION_OFFER, GREATEST_POWER_DESTRUCTION_SELECTIONS,
)
from ..drawing.captured_hand import CAPTURED_DRAW_CAST_FIELD
from ..bounded_hand_cast_model import BOUNDED_HAND_CAST_FIELD
from ..rules.ripple import RIPPLE_FIELD
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


def _controlled_counter_choice_snapshot(effect, rows, *, resolved_player=None):
    if CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD not in effect:
        return {}
    try:
        controlled_creature_counter_choice_dependencies(effect, resolved_player=resolved_player)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return {CONTROLLED_CREATURE_COUNTER_SNAPSHOTS: {
        row.ref: row.logical_object_id for row in rows
    }}


def _validate_opponent_counter_actor(query, stack_ref, player):
    stack = query.stack_object(stack_ref)
    if (stack is None or type(player) is not str or player not in query.active_seats
            or player == stack.controller or tuple(stack.targets) != (player,)):
        raise SemanticChoiceError("The counter recipient choice belongs to the announced opponent")


def _check_controlled_counter_choice(effect, rows, actor):
    if CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD not in effect:
        return
    snapshots = effect.get(CONTROLLED_CREATURE_COUNTER_SNAPSHOTS)
    if len(rows) != 1 or not isinstance(snapshots, Mapping):
        raise SemanticChoiceError("The selected creature's original incarnation is unavailable")
    row = rows[0]
    if (row is None or row.zone != "battlefield" or row.phased_out or row.controller != actor
            or "creature" not in row.types or not row.logical_object_id
            or row.logical_object_id != snapshots.get(row.ref)):
        raise SemanticChoiceError("The selected creature is no longer controlled and present")


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
        CONTROLLED_RETURN_FIELD,
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD,
        CONTROLLED_CREATURE_COUNTER_SNAPSHOTS,
        GREATEST_POWER_DESTRUCTION_FIELD,
        GREATEST_POWER_DESTRUCTION_OFFER,
        GREATEST_POWER_DESTRUCTION_SELECTIONS,
        GREATEST_POWER_EXILE_FIELD,
        GREATEST_POWER_EXILE_CHOICES,
        BOUNDED_HAND_CAST_FIELD,
        RIPPLE_FIELD,
        "cast_snapshot",
        "ripple_state",
        "demonstrate",
        "demonstrate_state",
        "gravestorm",
        "_gravestorm_copies",
        "copy_source",
        "player",
        "_options_fingerprint",
        "_return_candidates",
    )
    private_data: tuple[str, ...] = ("actor hand casting options",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "objects",
        "legal_actions.choice_schema.legal_refs",
        "cast_options",
        "legal_actions.choice_schema.legal_values",
        "legal_actions.choice_schema.cast_options",
        "legal_actions.choice_schema.cast_options_when",
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
        if 'gravestorm' in effect:
            from .gravestorm import prepare_gravestorm
            return prepare_gravestorm(effect,context)
        if 'demonstrate' in effect:
            from .demonstrate import prepare_demonstrate
            return prepare_demonstrate(effect,context)
        if RIPPLE_FIELD in effect:
            from .ripple import prepare_ripple
            return prepare_ripple(effect,context)
        if BOUNDED_HAND_CAST_FIELD in effect:
            from .bounded_hand_cast import prepare_bounded_hand_cast
            return prepare_bounded_hand_cast(effect, context)
        if CAPTURED_DRAW_CAST_FIELD in effect:
            from .captured_draw_cast import prepare_captured_draw_cast
            return prepare_captured_draw_cast(effect, context)
        if GREATEST_POWER_EXILE_FIELD in effect:
            return prepare_greatest_power_exile(effect, context)
        if GREATEST_POWER_DESTRUCTION_FIELD in effect:
            from .opponent_greatest_power_destruction import prepare_greatest_power_destruction
            return prepare_greatest_power_destruction(effect, context)
        if DISTINCT_SACRIFICE_FIELD in effect:
            from .distinct_sacrifices import prepare_distinct_sacrifices
            return prepare_distinct_sacrifices(effect, context)
        if CONTROLLED_RETURN_FIELD in effect:
            from .controlled_return import prepare_controlled_return
            return prepare_controlled_return(effect, context)
        refs = context.query.choice_candidate_refs()
        opponent = effect.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) == OPPONENT_CREATURE_COUNTER_CHOICE_VERSION
        if opponent:
            _validate_opponent_counter_actor(context.query, context.stack_ref, effect.get("player"))
            if effect.get("player") != context.actor:
                raise SemanticChoiceError("The opponent counter choice actor changed")
            refs = tuple(ref for ref in refs if context.query.counter_placement_allowed(ref, "deathtouch", 1))
        resolved_player = context.actor if opponent else None
        if not refs:
            _controlled_counter_choice_snapshot(effect, (), resolved_player=resolved_player)
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
                    **_controlled_counter_choice_snapshot(effect, rows, resolved_player=resolved_player),
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
        if 'gravestorm' in effect:
            raise SemanticChoiceError('Gravestorm delegates choices to its canonical copy instructions')
        if 'demonstrate' in effect:
            from .demonstrate import complete_demonstrate
            return complete_demonstrate(continuation,response,query)
        if RIPPLE_FIELD in effect:
            from .ripple import complete_ripple
            return complete_ripple(continuation,response,query)
        if BOUNDED_HAND_CAST_FIELD in effect:
            from .bounded_hand_cast import complete_bounded_hand_cast
            return complete_bounded_hand_cast(continuation, response, query)
        if CAPTURED_DRAW_CAST_FIELD in effect:
            from .captured_draw_cast import complete_captured_draw_cast
            return complete_captured_draw_cast(continuation, response, query)
        if GREATEST_POWER_EXILE_FIELD in effect:
            return complete_greatest_power_exile(continuation, response, query)
        if GREATEST_POWER_DESTRUCTION_FIELD in effect:
            from .opponent_greatest_power_destruction import complete_greatest_power_destruction
            return complete_greatest_power_destruction(continuation, response, query)
        if DISTINCT_SACRIFICE_FIELD in effect:
            from .distinct_sacrifices import complete_distinct_sacrifices
            return complete_distinct_sacrifices(continuation, response, query)
        if CONTROLLED_RETURN_FIELD in effect:
            from .controlled_return import complete_controlled_return
            return complete_controlled_return(continuation, response, query)
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
        _check_controlled_counter_choice(effect, rows, actor)
        if effect.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) == OPPONENT_CREATURE_COUNTER_CHOICE_VERSION:
            _validate_opponent_counter_actor(query, continuation.stack_ref, actor)
            fields = {"_choice_actor", "_legal_refs", "_minimum", "_maximum", "_stack_label",
                      CONTROLLED_CREATURE_COUNTER_SNAPSHOTS}
            try:
                controlled_creature_counter_choice_dependencies(
                    FrozenMap({key: val for key, val in effect.items() if key not in fields}),
                    resolved_player=actor,
                )
            except (ValueError, TypeError) as exc:
                raise SemanticChoiceError(str(exc)) from exc
            if not query.counter_placement_allowed(selected[0], "deathtouch", 1):
                raise SemanticChoiceError("The selected creature can no longer receive its counter")
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
                    "type_line": "Token Creature â€” Insect",
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
        from ..counter_placement_event_model import has_canonical_proliferate_choice_order

        try:
            canonical = has_canonical_proliferate_choice_order(effect)
        except ValueError as exc:
            raise SemanticChoiceError(str(exc)) from exc
        objects = tuple(
            row.ref
            for row in context.query.objects(zones=("battlefield",))
            if any(int(amount) > 0 for amount in row.counters.values())
        )
        if canonical:
            objects = tuple(sorted(objects))
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
