"""Offer or commit counter movement between the original public objects."""

from dataclasses import dataclass

from ..counter_snapshot import permanent_counter_snapshot

from ..fixed_counter_movement_model import (
    CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED, FIXED_COUNTER_MOVEMENT_CAPABILITY,
    FIXED_COUNTER_MOVEMENT_OPERATION, FixedCounterMovementSpec,
    OPTIONAL_CHOSEN_CONTROLLED_CREATURES, SOURCE_GIVES_COUNTERS_THEN_DRAW, SOURCE_RECEIVES_MISSING_KINDS,
)
from ..replacement.immutable import FrozenMap
from ..semantic_runtime.counter_movement_intent import MoveCountersIntent, MoveCounterSetIntent
from .graft import can_move_plus_one_counter
from .counter_movement_quantities import (
    QUANTITIES_FIELDS, complete_counter_movement_quantities, prepare_counter_movement_quantities,
    prepare_missing_counter_kinds,
)
from .model import (
    AutoContinue, ScalarChoice, SemanticChoiceCompletion, SemanticChoiceError,
    SemanticChoicePreparation, SemanticChoiceRequest,
)


_IDENTITIES = ("from_identity", "to_identity")
_OFFERED_KINDS = "offered_counter_kinds"
_CHOSEN_RESPONSE_FIELDS = {"counter_kind", "action", "action_id", "a", "cap", "reason", "plan",
                           "next", "choice_schema", "label"}


def _bound_spec(effect, actor, query):
    try:
        spec = FixedCounterMovementSpec.from_effect(effect, bound=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    if effect["player"] != actor or actor not in query.active_seats:
        raise SemanticChoiceError("The original controller owns the counter movement")
    return spec


def _pin_movement(effect, spec, context):
    source_ref, recipient_ref = effect["from_ref"], effect["to_ref"]
    if source_ref is None or recipient_ref is None:
        return None
    source = context.query.object(source_ref, zones=("battlefield",))
    recipient = context.query.object(recipient_ref, zones=("battlefield",))
    if (source is None or recipient is None
            or spec.receiver_is_ability_source and recipient.logical_object_id != context.source_logical_object_id
            or (spec.moves_all_source_counters or spec.form == SOURCE_GIVES_COUNTERS_THEN_DRAW) and source.logical_object_id != context.source_logical_object_id):
        return None
    pinned = FrozenMap({**effect, "from_identity": source.logical_object_id, "to_identity": recipient.logical_object_id})
    return pinned if (spec.is_source_bound or spec.chooses_counter_kind or spec.chooses_counter_quantities or spec.moves_all_source_counters
                      or can_move_plus_one_counter(pinned, context.query)) else None


def _available_counter_kinds(effect, query):
    donor = query.object(effect["from_ref"], zones=("battlefield",))
    recipient = query.object(effect["to_ref"], zones=("battlefield",))
    if (donor is None or recipient is None or donor.object_id == recipient.object_id
            or donor.phased_out or recipient.phased_out
            or donor.logical_object_id != effect["from_identity"]
            or recipient.logical_object_id != effect["to_identity"]):
        return ()
    opposing_donor = effect["form"] == CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED
    if (donor.controller != effect["player"]) != opposing_donor:
        return ()
    if opposing_donor and (recipient.controller != effect["player"] or "creature" not in donor.types or "creature" not in recipient.types):
        return ()
    if effect["form"] == OPTIONAL_CHOSEN_CONTROLLED_CREATURES and (
            recipient.controller != effect["player"] or "creature" not in donor.types or "creature" not in recipient.types):
        return ()
    try:
        amounts = permanent_counter_snapshot(donor.counters)
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return tuple(kind for kind in amounts if query.counter_placement_allowed(recipient.ref, kind, 1))


def _prepare_chosen_movement(pinned, context, *, optional):
    kinds = _available_counter_kinds(pinned, context.query) if pinned is not None else ()
    if not kinds:
        return SemanticChoicePreparation(request=None, continuation_effect=FrozenMap(pinned or {}),
            auto_continue=AutoContinue("The original objects cannot move a counter"))
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(prompt="You may choose a counter to move." if optional else "Choose a counter to move.",
            choice=ScalarChoice(field_name="counter_kind", legal_values=kinds, optional=optional),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": FIXED_COUNTER_MOVEMENT_OPERATION,
                                      "source": pinned["from_ref"], "recipient": pinned["to_ref"]})),
        continuation_effect=FrozenMap({**pinned, _OFFERED_KINDS: kinds}),
    )


def _complete_chosen_movement(continuation, response, query, *, optional):
    pinned = continuation.effect
    actor = continuation.semantic_frame.controller
    stack = query.stack_object(continuation.stack_ref)
    offered = pinned.get(_OFFERED_KINDS)
    if (stack is None or stack.controller != actor or set(response) - _CHOSEN_RESPONSE_FIELDS
            or any(type(pinned.get(field)) is not str or not pinned[field] for field in _IDENTITIES)
            or not isinstance(offered, tuple) or not offered
            or any(type(kind) is not str or not kind for kind in offered)
            or offered != tuple(sorted(set(offered)))):
        raise SemanticChoiceError("The original counter movement requires its pinned public choice")
    kind = response.get("counter_kind")
    if kind is None and optional:
        return SemanticChoiceCompletion()
    if type(kind) is not str or kind not in offered or kind not in _available_counter_kinds(pinned, query):
        raise SemanticChoiceError("Choose one currently movable offered counter kind")
    return SemanticChoiceCompletion(intents=(_intent(pinned, continuation.stack_ref, counter_name=kind),))


def _all_source_counter_quantities(effect, query):
    donor = query.object(effect["from_ref"], zones=("battlefield",))
    recipient = query.object(effect["to_ref"], zones=("battlefield",))
    if (donor is None or recipient is None or donor.object_id == recipient.object_id
            or donor.phased_out or recipient.phased_out or "creature" not in recipient.types
            or donor.logical_object_id != effect["from_identity"]
            or recipient.logical_object_id != effect["to_identity"]):
        return ()
    try:
        return tuple(sorted(permanent_counter_snapshot(donor.counters).items()))
    except ValueError as exc:
        raise SemanticChoiceError(str(exc)) from exc


def _prepare_all_source_movement(effect, pinned, context):
    quantities = _all_source_counter_quantities(pinned, context.query) if pinned is not None else ()
    if not any(context.query.counter_placement_allowed(pinned["to_ref"], name, amount)
               for name, amount in quantities):
        return SemanticChoicePreparation(
            request=None, continuation_effect=FrozenMap(pinned or effect),
            auto_continue=AutoContinue("The original objects cannot move their counters"),
        )
    return SemanticChoicePreparation(
        request=SemanticChoiceRequest(
            prompt=f"Move all counters from {pinned['from_ref']} onto {pinned['to_ref']}?",
            choice=ScalarChoice(field_name="choice", legal_values=("move", "decline")),
            public_context=FrozenMap({"stack": context.stack_ref, "operation": FIXED_COUNTER_MOVEMENT_OPERATION,
                                      "source": pinned["from_ref"], "recipient": pinned["to_ref"]}),
        ),
        continuation_effect=pinned,
    )


def _complete_all_source_movement(continuation, response, query):
    pinned = continuation.effect
    choice = response.get("choice")
    actor = continuation.semantic_frame.controller
    stack = query.stack_object(continuation.stack_ref)
    if (set(response) - ((_CHOSEN_RESPONSE_FIELDS - {"counter_kind"}) | {"choice"})
            or type(choice) is not str or choice not in {"move", "decline"}
            or stack is None or stack.controller != actor
            or any(type(pinned.get(field)) is not str or not pinned[field] for field in _IDENTITIES)):
        raise SemanticChoiceError("Choose whether to move all counters from the original source")
    quantities = _all_source_counter_quantities(pinned, query)
    if choice == "decline" or not quantities:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(intents=(MoveCounterSetIntent(
        actor=actor, from_ref=pinned["from_ref"], from_identity=pinned["from_identity"],
        to_ref=pinned["to_ref"], to_identity=pinned["to_identity"], amounts=FrozenMap(dict(quantities)),
        reason="Move all counters from the original permanent", source_ref=continuation.stack_ref,
        all_to_creature=True,
    ),))


def _intent(effect, stack_ref, *, counter_name="+1/+1"):
    return MoveCountersIntent(
        actor=effect["player"], from_ref=effect["from_ref"], from_identity=effect["from_identity"],
        to_ref=effect["to_ref"], to_identity=effect["to_identity"], counter_name=counter_name, amount=1,
        reason=f"Move a {counter_name} counter", source_ref=stack_ref,
    )


@dataclass(frozen=True, slots=True)
class FixedCounterMovementHandler:
    operation: str = FIXED_COUNTER_MOVEMENT_OPERATION
    handler_id: str = "choice.counter.fixed-movement.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 115.3", "CR 122.5", "CR 608.2b", "CR 608.2d")
    capability_dependencies: tuple[str, ...] = (FIXED_COUNTER_MOVEMENT_CAPABILITY,)
    continuation_fields: tuple[str, ...] = (
        "schema_version", "form", "player", "from_ref", "to_ref", *_IDENTITIES, _OFFERED_KINDS, *QUANTITIES_FIELDS,
    )
    private_data: tuple[str, ...] = ()
    projected_fields: tuple[str, ...] = (
        "prompt", "source", "recipient", "legal_actions.choice_schema.legal_values",
        "legal_actions.choice_schema.minimum", "legal_actions.choice_schema.maximum",
    )
    mutation_path: tuple[str, ...] = ("MoveCountersIntent", "rules.counter_movement.execute_counter_movement")
    replay_fixture: str = "fixed-one-counter-movement"
    test_modules: tuple[str, ...] = (
        "tests.test_fixed_one_counter_movement", "tests.test_chosen_counter_movement", "tests.test_resourceful_defense",
    )

    def prepare(self, effect, context):
        resuming = any(key in effect for key in _IDENTITIES)
        instruction = {key: value for key, value in effect.items() if key not in (*_IDENTITIES, *QUANTITIES_FIELDS)}
        spec = _bound_spec(instruction, context.actor, context.query)
        pinned = _pin_movement(instruction, spec, context)
        if spec.chooses_counter_quantities:
            return prepare_counter_movement_quantities(effect, pinned, context)
        if any(key in effect for key in QUANTITIES_FIELDS):
            raise SemanticChoiceError("Only chosen quantities use a counter selection sequence")
        if spec.moves_all_source_counters:
            if resuming:
                raise SemanticChoiceError("An all-counter movement resumes from its sealed choice")
            return _prepare_all_source_movement(effect, pinned, context)
        if spec.chooses_counter_kind:
            if resuming:
                raise SemanticChoiceError("Chosen counter movement resumes from its sealed choice")
            return _prepare_chosen_movement(pinned, context, optional=spec.optional)
        if resuming and (spec.optional or pinned != FrozenMap(effect)):
            raise SemanticChoiceError("The original counter movement changed before replacement resume")
        if spec.form == SOURCE_RECEIVES_MISSING_KINDS:
            return prepare_missing_counter_kinds(pinned, context)
        if pinned is None:
            return SemanticChoicePreparation(
                request=None, continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue("The original objects cannot move a +1/+1 counter"),
            )
        if not spec.optional:
            return SemanticChoicePreparation(
                request=None, continuation_effect=pinned, preparation_intents=(_intent(pinned, context.stack_ref),),
                auto_continue=AutoContinue("Move the counter between the original objects"),
            )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=f"Move a +1/+1 counter from {pinned['from_ref']} onto {pinned['to_ref']}?",
                choice=ScalarChoice(field_name="choice", legal_values=("move", "decline")),
                public_context=FrozenMap({"stack": context.stack_ref, "operation": self.operation,
                    "source": pinned["from_ref"], "recipient": pinned["to_ref"]}),
            ),
            continuation_effect=pinned,
        )

    def complete(self, continuation, response, query):
        pinned = continuation.effect
        effect = {key: value for key, value in pinned.items() if key not in (*_IDENTITIES, _OFFERED_KINDS, *QUANTITIES_FIELDS)}
        spec = _bound_spec(effect, continuation.semantic_frame.controller, query)
        if spec.chooses_counter_quantities:
            return complete_counter_movement_quantities(continuation, response, query)
        if any(key in pinned for key in QUANTITIES_FIELDS):
            raise SemanticChoiceError("Only chosen quantities use a counter selection sequence")
        if spec.moves_all_source_counters:
            if _OFFERED_KINDS in pinned:
                raise SemanticChoiceError("An all-counter movement cannot offer individual kinds")
            return _complete_all_source_movement(continuation, response, query)
        if spec.chooses_counter_kind:
            return _complete_chosen_movement(continuation, response, query, optional=spec.optional)
        if _OFFERED_KINDS in pinned:
            raise SemanticChoiceError("Fixed counter movement cannot carry a chosen counter offer")
        choice = response.get("choice")
        if (not spec.optional or type(choice) is not str or choice not in {"move", "decline"}
                or any(type(pinned.get(key)) is not str or not pinned[key] for key in _IDENTITIES)):
            raise SemanticChoiceError("The original counter movement requires move or decline")
        if choice == "decline" or not can_move_plus_one_counter(pinned, query):
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(intents=(_intent(pinned, continuation.stack_ref),))
