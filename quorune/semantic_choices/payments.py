from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..compiler.optional_payment_templates import (
    OPTIONAL_MANA_PAYMENT_OPERATION,
)
from ..replacement.immutable import FrozenMap, freeze_value
from ..semantic_runtime.intents import (
    CounterStackIntent,
    EliminatePlayersIntent,
    MoveObjectsSimultaneouslyIntent,
    PayLifeIntent,
    PayManaCostIntent,
    PlaceCountersIntent,
    ZoneMoveIntent,
)
from ..trigger_participation import (
    TriggerParticipationError,
    WardSpec,
)
from ..zone_trigger_events import ZoneTransitionKind
from .context import SemanticChoiceContext, SemanticChoiceQuery
from .model import (
    AutoContinue,
    ObjectChoice,
    ScalarChoice,
    SemanticChoiceCompletion,
    SemanticChoiceContinuation,
    SemanticChoiceError,
    SemanticChoicePreparation,
    SemanticChoiceRequest,
)


_MANA_KEYS = ("GENERIC", "W", "U", "B", "R", "G", "C")
_CUMULATIVE_MODES = frozenset({"cumulative", "cumulative_life"})


def _requirements(value: Mapping[str, Any]) -> dict[str, int]:
    result = {key: int(value.get(key, 0)) for key in _MANA_KEYS}
    if any(amount < 0 for amount in result.values()):
        raise SemanticChoiceError("Payment costs cannot be negative")
    return result


def _strict_fixed_mana_requirements(
    value: Any,
    *,
    label: str,
    require_positive: bool,
) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise SemanticChoiceError(
            f"{label} requires a fixed mana cost"
        )
    unknown = sorted(set(value) - set(_MANA_KEYS))
    if unknown:
        raise SemanticChoiceError(
            f"{label} cost has unknown mana fields: "
            + ", ".join(str(field) for field in unknown)
        )
    if any(type(amount) is not int or amount < 0 for amount in value.values()):
        raise SemanticChoiceError(
            f"{label} mana amounts must be nonnegative integers"
        )
    result = {key: int(value.get(key, 0)) for key in _MANA_KEYS}
    if require_positive and not any(result.values()):
        raise SemanticChoiceError(
            f"{label} fixed mana cost must be positive"
        )
    return result


def _strict_positive_amount(value: Any, *, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise SemanticChoiceError(f"{label} must be a positive integer")
    return value


def _current_pay_or_sacrifice_source(
    query: SemanticChoiceQuery,
    source_ref: str,
    logical_object_id: str | None,
) -> Any | None:
    source = query.object(source_ref, zones=("battlefield",))
    if source is None or source.phased_out:
        return None
    if logical_object_id and source.logical_object_id != logical_object_id:
        return None
    return source


def _completion_requirements(
    mode: str,
    effect: Mapping[str, Any],
) -> dict[str, int]:
    value = effect.get("_requirements", FrozenMap())
    if mode == "effect":
        return _strict_fixed_mana_requirements(
            value,
            label="Optional effect payment continuation",
            require_positive=True,
        )
    if mode not in {"cumulative", "echo"}:
        return _requirements(value)
    return _strict_fixed_mana_requirements(
        value,
        label=(
            "Cumulative upkeep continuation"
            if mode == "cumulative"
            else "Echo continuation"
        ),
        require_positive=False,
    )


def _payment_choice(response: Mapping[str, Any]) -> bool:
    value = response.get("pay", False)
    if type(value) is not bool:
        raise SemanticChoiceError("Optional payment choice must be boolean")
    return value


def _validated_paid_effects(
    effect: Mapping[str, Any],
    *,
    actor: str,
    query: SemanticChoiceQuery,
) -> tuple[str, tuple[FrozenMap, ...]]:
    player = effect.get("player")
    if (
        type(player) is not str
        or player != actor
        or player not in query.active_seats
    ):
        raise SemanticChoiceError(
            "Optional effect payment must be issued to its active controller"
        )
    values = effect.get("effects")
    if (
        not isinstance(values, Sequence)
        or isinstance(values, (str, bytes))
        or len(values) != 1
        or not isinstance(values[0], Mapping)
    ):
        raise SemanticChoiceError(
            "Optional effect payment requires one represented instruction"
        )
    nested = values[0]
    if nested.get("op") in {
        "offer_optional_effect",
        OPTIONAL_MANA_PAYMENT_OPERATION,
    }:
        raise SemanticChoiceError("Optional effect payments cannot nest")
    from .optional_effect import _represented_effect

    _represented_effect(nested, actor=player, query=query)
    frozen = freeze_value(nested)
    if not isinstance(frozen, FrozenMap):
        raise SemanticChoiceError(
            "Optional effect payment continuation is malformed"
        )
    return player, (frozen,)


def _validate_optional_effect_payment(
    effect: Mapping[str, Any],
    context: SemanticChoiceContext,
    *,
    operation: str,
) -> None:
    allowed = {"op", "player", "cost", "effects"}
    unknown = sorted(set(effect) - allowed)
    missing = sorted(allowed - set(effect))
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unknown " + ", ".join(unknown))
        raise SemanticChoiceError(
            "Malformed optional effect payment: " + "; ".join(details)
        )
    if effect.get("op") != operation:
        raise SemanticChoiceError(
            "Optional effect payment operation is malformed"
        )
    _strict_fixed_mana_requirements(
        effect.get("cost"),
        label="Optional effect payment",
        require_positive=True,
    )
    _validated_paid_effects(
        effect,
        actor=context.actor,
        query=context.query,
    )


def _pay_or_sacrifice_details(
    mode: str,
    effect: Mapping[str, Any],
    query: SemanticChoiceQuery,
    source_ref: str,
) -> dict[str, Any]:
    expected_logical = str(effect.get("_source_logical_object_id") or "")
    source = _current_pay_or_sacrifice_source(
        query,
        source_ref,
        expected_logical,
    )
    if source is None:
        raise SemanticChoiceError(
            f"The {mode.replace('_', '-')} source condition changed during its choice"
        )
    details: dict[str, Any] = {"source": source_ref}
    if mode in _CUMULATIVE_MODES:
        details["age_counters"] = source.counters.get("age", 0)
    return details


def _declined_pay_or_sacrifice(
    mode: str,
    effect: Mapping[str, Any],
    actor: str,
    query: SemanticChoiceQuery,
) -> SemanticChoiceCompletion:
    source_ref = str(effect.get("_source_ref") or "")
    expected_logical = str(effect.get("_source_logical_object_id") or "")
    source = _current_pay_or_sacrifice_source(
        query,
        source_ref,
        expected_logical,
    )
    if source is None or source.controller != actor:
        return SemanticChoiceCompletion()
    return SemanticChoiceCompletion(
        intents=(
            ZoneMoveIntent(
                actor=actor,
                object_ref=source_ref,
                expected_zones=("battlefield",),
                destination="graveyard",
                reason=(
                    "cumulative upkeep not paid"
                    if mode in _CUMULATIVE_MODES
                    else "Echo not paid"
                ),
                controlled_only=True,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class OptionalPaymentHandler:
    operation: str
    handler_id: str
    mode: str
    prompt: str
    default_cost: FrozenMap
    schema_version: int = 1
    rule_references: tuple[str, ...] = ("CR 118.12", "CR 608.2d")
    capability_dependencies: tuple[str, ...] = ()
    continuation_fields: tuple[str, ...] = (
        "player",
        "cost",
        "source",
        "stack",
        "beneficiary",
        "stage",
        "_choice_actor",
        "_requirements",
        "_source_ref",
        "_source_logical_object_id",
        "_stack_label",
    )
    private_data: tuple[str, ...] = ("actor payable mana",)
    projected_fields: tuple[str, ...] = (
        "prompt",
        "cost",
        "payable",
        "legal_actions.choice_schema.legal_values",
    )
    mutation_path: tuple[str, ...] = (
        "PayManaCostIntent",
        "typed decline intent",
    )
    replay_fixture: str = "semantic-choice-optional-payment"
    test_modules: tuple[str, ...] = (
        "tests.test_semantic_choice_characterization",
        "tests.test_exact_zimone_closure",
    )

    def _cost_and_source(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> tuple[dict[str, int], str | None, int | None]:
        if self.mode == "effect":
            _validate_optional_effect_payment(
                effect,
                context,
                operation=self.operation,
            )
        if self.mode not in {"cumulative", "echo"}:
            return (
                _requirements(
                    effect.get("cost")
                    if isinstance(effect.get("cost"), Mapping)
                    else self.default_cost
                ),
                None,
                None,
            )
        source_ref = str(effect.get("source") or "")
        source = _current_pay_or_sacrifice_source(
            context.query,
            source_ref,
            context.source_logical_object_id,
        )
        if source is None:
            raise SemanticChoiceError(
                "The pay-or-sacrifice source condition is no longer true"
            )
        if self.mode == "echo":
            return (
                _strict_fixed_mana_requirements(
                    effect.get("cost"),
                    label="Echo",
                    require_positive=False,
                ),
                source.ref,
                None,
            )
        per_counter = _strict_fixed_mana_requirements(
            effect.get("cost_per_counter"),
            label="Cumulative upkeep",
            require_positive=True,
        )
        age = int(source.counters.get("age", 0))
        return (
            {key: amount * age for key, amount in per_counter.items()},
            source.ref,
            age,
        )

    def _prepare_cumulative_life(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        allowed = {"op", "player", "source", "life_per_counter", "stage"}
        unknown = sorted(set(effect) - allowed)
        required = {"op", "player", "source", "life_per_counter"}
        missing = sorted(required - set(effect))
        if missing or unknown:
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            raise SemanticChoiceError(
                "Malformed fixed-life cumulative upkeep effect: "
                + "; ".join(details)
            )
        if effect.get("op") != self.operation:
            raise SemanticChoiceError(
                "Fixed-life cumulative upkeep operation is malformed"
            )
        if effect.get("player") != context.actor:
            raise SemanticChoiceError(
                "Fixed-life cumulative upkeep player is malformed"
            )
        stage = effect.get("stage")
        if stage not in {None, "pay"}:
            raise SemanticChoiceError(
                "Unknown fixed-life cumulative upkeep stage"
            )
        per_counter = _strict_positive_amount(
            effect.get("life_per_counter"),
            label="Cumulative upkeep life per age counter",
        )
        source_ref = str(effect.get("source") or "")
        source = _current_pay_or_sacrifice_source(
            context.query,
            source_ref,
            context.source_logical_object_id,
        )
        if source is None:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason=(
                        "fixed-life cumulative-upkeep intervening condition "
                        "is false"
                    )
                ),
            )
        if stage is None:
            pay_effect = FrozenMap({**dict(effect), "stage": "pay"})
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                preparation_intents=(
                    PlaceCountersIntent(
                        actor=source.controller,
                        object_refs=(source_ref,),
                        counter_name="age",
                        amount=1,
                        reason=context.stack_label,
                        source_ref=context.source_ref,
                    ),
                ),
                auto_continue=AutoContinue(
                    reason="age counter placement committed",
                    prepend_effects=(pay_effect,),
                ),
            )
        age = source.counters.get("age", 0)
        if type(age) is not int or age < 0:
            raise SemanticChoiceError(
                "Cumulative upkeep age counters are malformed"
            )
        life_cost = per_counter * age
        payable = context.query.player_life(context.actor) >= life_cost
        continuation_effect = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_life_payment": life_cost,
                "_source_ref": source.ref,
                "_source_logical_object_id": (
                    context.source_logical_object_id
                ),
                "_stack_label": context.stack_label,
            }
        )
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    f"Pay {life_cost} life for cumulative upkeep or sacrifice "
                    f"{source.printed_name}."
                ),
                choice=ScalarChoice(
                    field_name="pay",
                    legal_values=(True, False) if payable else (False,),
                ),
                public_context=FrozenMap(
                    {
                        "stack": context.stack_ref,
                        "operation": self.operation,
                        "life_cost": life_cost,
                        "age_counters": age,
                        "payable": payable,
                    }
                ),
            ),
            continuation_effect=continuation_effect,
        )

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        if self.mode == "cumulative_life":
            return self._prepare_cumulative_life(effect, context)
        if self.mode == "cumulative":
            allowed = {"op", "player", "source", "cost_per_counter", "stage"}
            unknown = sorted(set(effect) - allowed)
            required = {"op", "player", "source", "cost_per_counter"}
            missing = sorted(required - set(effect))
            if missing or unknown:
                details = []
                if missing:
                    details.append("missing " + ", ".join(missing))
                if unknown:
                    details.append("unknown " + ", ".join(unknown))
                raise SemanticChoiceError(
                    "Malformed cumulative upkeep effect: " + "; ".join(details)
                )
            stage = effect.get("stage")
            if stage not in {None, "pay"}:
                raise SemanticChoiceError("Unknown cumulative upkeep stage")
            _strict_fixed_mana_requirements(
                effect.get("cost_per_counter"),
                label="Cumulative upkeep",
                require_positive=True,
            )
            source_ref = str(effect.get("source") or "")
            source = _current_pay_or_sacrifice_source(
                context.query, source_ref, context.source_logical_object_id
            )
            if source is None:
                return SemanticChoicePreparation(
                    request=None,
                    continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue(
                        reason=(
                            "cumulative-upkeep intervening condition is false"
                        )
                    ),
                )
            if stage is None:
                pay_effect = FrozenMap({**dict(effect), "stage": "pay"})
                return SemanticChoicePreparation(
                    request=None,
                    continuation_effect=FrozenMap(effect),
                    preparation_intents=(
                        PlaceCountersIntent(
                            actor=source.controller,
                            object_refs=(source_ref,),
                            counter_name="age",
                            amount=1,
                            reason=context.stack_label,
                            source_ref=context.source_ref,
                        ),
                    ),
                    auto_continue=AutoContinue(
                        reason="age counter placement committed",
                        prepend_effects=(pay_effect,),
                    ),
                )
        elif self.mode == "echo":
            allowed = {"op", "player", "source", "cost"}
            unknown = sorted(set(effect) - allowed)
            required = allowed
            missing = sorted(required - set(effect))
            if missing or unknown:
                details = []
                if missing:
                    details.append("missing " + ", ".join(missing))
                if unknown:
                    details.append("unknown " + ", ".join(unknown))
                raise SemanticChoiceError(
                    "Malformed Echo effect: " + "; ".join(details)
                )
            source_ref = str(effect.get("source") or "")
            source = _current_pay_or_sacrifice_source(
                context.query,
                source_ref,
                context.source_logical_object_id,
            )
            if source is None:
                return SemanticChoicePreparation(
                    request=None,
                    continuation_effect=FrozenMap(effect),
                    auto_continue=AutoContinue(
                        reason="Echo source is no longer the same permanent"
                    ),
                )
        requirements, source_ref, age = self._cost_and_source(effect, context)
        payable = context.query.cost_is_affordable(
            context.actor,
            requirements,
        )
        continuation_effect = FrozenMap(
            {
                **dict(effect),
                "_choice_actor": context.actor,
                "_requirements": requirements,
                "_source_ref": source_ref,
                "_source_logical_object_id": (
                    context.source_logical_object_id
                ),
                "_stack_label": context.stack_label,
            }
        )
        public: dict[str, Any] = {
            "stack": context.stack_ref,
            "operation": self.operation,
            "cost": requirements,
            "payable": payable,
        }
        if self.mode == "counter":
            public["target_stack"] = effect.get("stack")
        elif self.mode == "remora":
            public["beneficiary"] = effect.get("beneficiary")
        elif self.mode == "cumulative":
            source = context.query.object(source_ref or "")
            public["age_counters"] = age
            prompt = (
                f"Pay cumulative upkeep {requirements} or sacrifice "
                f"{source.printed_name if source else source_ref}."
            )
        elif self.mode == "echo":
            source = context.query.object(source_ref or "")
            prompt = (
                f"Pay Echo {requirements} or sacrifice "
                f"{source.printed_name if source else source_ref}."
            )
        else:
            prompt = self.prompt
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=(
                    prompt
                    if self.mode in {"cumulative", "echo"}
                    else self.prompt
                ),
                choice=ScalarChoice(
                    field_name="pay",
                    legal_values=(True, False) if payable else (False,),
                ),
                public_context=FrozenMap(public),
            ),
            continuation_effect=continuation_effect,
            preparation_intents=(),
        )

    def _complete_cumulative_life(
        self,
        effect: Mapping[str, Any],
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
        actor: str,
    ) -> SemanticChoiceCompletion:
        life_payment = effect.get("_life_payment")
        if type(life_payment) is not int or life_payment < 0:
            raise SemanticChoiceError(
                "Cumulative upkeep life payment is malformed"
            )
        pay = _payment_choice(response)
        if pay and query.player_life(actor) < life_payment:
            raise SemanticChoiceError(
                "The cumulative upkeep life payment is no longer payable"
            )
        if pay:
            source_ref = str(effect.get("_source_ref") or "")
            _pay_or_sacrifice_details(
                self.mode,
                effect,
                query,
                source_ref,
            )
            return SemanticChoiceCompletion(
                intents=(
                    PayLifeIntent(
                        actor=actor,
                        player=actor,
                        amount=life_payment,
                        reason=str(effect["_stack_label"]),
                    ),
                )
            )
        return _declined_pay_or_sacrifice(
            self.mode,
            effect,
            actor,
            query,
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        actor = str(effect["_choice_actor"])
        if self.mode == "cumulative_life":
            return self._complete_cumulative_life(
                effect, response, query, actor
            )
        requirements = _completion_requirements(self.mode, effect)
        pay = _payment_choice(response)
        if pay and not query.cost_is_affordable(actor, requirements):
            raise SemanticChoiceError(
                "The optional payment is no longer payable"
            )
        label = str(effect["_stack_label"])
        if pay:
            event_code = {
                "counter": "counter.unless.paid",
                "cumulative": "cumulative_upkeep.paid",
                "echo": "echo.paid",
                "effect": "effect.optional_mana.paid",
                "remora": "mystic_remora.paid",
                "pact": "pact.paid",
            }[self.mode]
            source_ref = str(effect.get("_source_ref") or "") or None
            message = {
                "counter": (
                    f"{actor} paid to prevent {effect.get('stack')} from being countered."
                ),
                "cumulative": f"{actor} paid cumulative upkeep for {source_ref}.",
                "echo": f"{actor} paid Echo for {source_ref}.",
                "effect": f"{actor} paid for an optional triggered effect.",
                "remora": f"{actor} paid for Mystic Remora.",
                "pact": f"{actor} paid the delayed Pact cost.",
            }[self.mode]
            details: dict[str, Any] = {"cost": requirements}
            if self.mode == "counter":
                details["stack"] = effect.get("stack")
            elif self.mode in {"cumulative", "echo"}:
                details.update(
                    _pay_or_sacrifice_details(
                        self.mode,
                        effect,
                        query,
                        source_ref or "",
                    )
                )
            else:
                details["stack"] = continuation.stack_ref
            completion = SemanticChoiceCompletion(
                intents=(
                    PayManaCostIntent(
                        actor=actor,
                        player=actor,
                        requirements=FrozenMap(requirements),
                        reason=label,
                        event_code=event_code,
                        message=message,
                        details=FrozenMap(details),
                        changed_object_ref=source_ref,
                    ),
                )
            )
            if self.mode == "effect":
                _player, paid_effects = _validated_paid_effects(
                    effect,
                    actor=actor,
                    query=query,
                )
                return SemanticChoiceCompletion(
                    intents=completion.intents,
                    prepend_effects=paid_effects,
                )
            return completion
        if self.mode == "effect":
            return SemanticChoiceCompletion()
        if self.mode == "counter":
            target = str(effect.get("stack") or "")
            if query.stack_object(target) is None:
                return SemanticChoiceCompletion()
            return SemanticChoiceCompletion(
                intents=(
                    CounterStackIntent(
                        actor=actor,
                        stack_ref=target,
                        reason=label,
                        countered_by=actor,
                    ),
                )
            )
        if self.mode in {*_CUMULATIVE_MODES, "echo"}:
            return _declined_pay_or_sacrifice(
                self.mode,
                effect,
                actor,
                query,
            )
        if self.mode == "pact":
            return SemanticChoiceCompletion(
                intents=(
                    EliminatePlayersIntent(
                        actor=actor,
                        players=(actor,),
                        reason="failed to pay Pact of Negation",
                    ),
                )
            )
        beneficiary = str(effect.get("beneficiary") or "")
        if beneficiary not in query.active_seats:
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(
            prepend_effects=(
                FrozenMap(
                    {
                        "op": "choose_option",
                        "player": beneficiary,
                        "prompt": "Draw a card with Mystic Remora?",
                        "options": [
                            {"id": "draw", "label": "Draw a card"},
                            {"id": "decline", "label": "Do not draw"},
                        ],
                        "then_by_choice": {
                            "draw": [
                                {
                                    "op": "draw",
                                    "player": beneficiary,
                                    "count": 1,
                                    "private": True,
                                }
                            ],
                            "decline": [],
                        },
                    }
                ),
            )
        )


@dataclass(frozen=True, slots=True)
class WardPaymentChoiceHandler:
    """Resolve one typed Ward payment or counter its targeting stack item."""

    operation: str = "ward_counter_unless_pay"
    handler_id: str = "choice.payment.ward-fixed-public.v1"
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "CR 118.4",
        "CR 118.5",
        "CR 702.21a",
        "CR 702.21b",
    )
    capability_dependencies: tuple[str, ...] = (
        "trigger.keyword.ward.fixed_generic",
        "trigger.keyword.ward.fixed_nonmana",
    )
    continuation_fields: tuple[str, ...] = (
        "player",
        "stack",
        "ward_spec",
        "_choice_actor",
        "_requirements",
        "_life_payment",
        "_legal_objects",
        "_stack_label",
    )
    private_data: tuple[str, ...] = (
        "payer hand identities for discard-one Ward",
    )
    projected_fields: tuple[str, ...] = (
        "prompt",
        "payment_kind",
        "payable",
        "legal_actions.choice_schema",
    )
    mutation_path: tuple[str, ...] = (
        "PayManaCostIntent",
        "PayLifeIntent",
        "MoveObjectsSimultaneouslyIntent",
        "CounterStackIntent",
    )
    replay_fixture: str = "semantic-choice-ward-payment"
    test_modules: tuple[str, ...] = (
        "tests.test_trigger_processing_owner",
    )

    @staticmethod
    def _spec(effect: Mapping[str, Any]) -> WardSpec:
        try:
            return WardSpec.from_dict(effect.get("ward_spec"))
        except (TriggerParticipationError, TypeError) as exc:
            raise SemanticChoiceError("Ward payment spec is malformed") from exc

    @staticmethod
    def _validate_effect(
        effect: Mapping[str, Any], context: SemanticChoiceContext
    ) -> WardSpec:
        expected = {"op", "player", "stack", "ward_spec"}
        if set(effect) != expected or effect.get("op") != (
            "ward_counter_unless_pay"
        ):
            raise SemanticChoiceError("Ward payment effect is malformed")
        if effect.get("player") != context.actor:
            raise SemanticChoiceError("Ward payer is malformed")
        target = effect.get("stack")
        if type(target) is not str or not target:
            raise SemanticChoiceError("Ward target stack reference is malformed")
        return WardPaymentChoiceHandler._spec(effect)

    def prepare(
        self,
        effect: Mapping[str, Any],
        context: SemanticChoiceContext,
    ) -> SemanticChoicePreparation:
        spec = self._validate_effect(effect, context)
        target = str(effect["stack"])
        if context.query.stack_object(target) is None:
            return SemanticChoicePreparation(
                request=None,
                continuation_effect=FrozenMap(effect),
                auto_continue=AutoContinue(
                    reason="Ward target is no longer on the stack"
                ),
            )
        public: dict[str, Any] = {
            "stack": context.stack_ref,
            "operation": self.operation,
            "target_stack": target,
            "payment_kind": spec.payment_kind,
        }
        continuation: dict[str, Any] = {
            **dict(effect),
            "_choice_actor": context.actor,
            "_stack_label": context.stack_label,
        }
        if spec.generic_cost is not None:
            requirements = {"GENERIC": spec.generic_cost}
            payable = context.query.cost_is_affordable(
                context.actor, requirements
            )
            choice: ScalarChoice | ObjectChoice = ScalarChoice(
                field_name="pay",
                legal_values=(True, False) if payable else (False,),
            )
            continuation["_requirements"] = requirements
            public.update({"cost": requirements, "payable": payable})
            prompt = "Pay the Ward mana cost to prevent the stack item from being countered."
        elif spec.life_payment is not None:
            payable = (
                context.query.player_life(context.actor)
                >= spec.life_payment
            )
            choice = ScalarChoice(
                field_name="pay",
                legal_values=(True, False) if payable else (False,),
            )
            continuation["_life_payment"] = spec.life_payment
            public.update(
                {"life_payment": spec.life_payment, "payable": payable}
            )
            prompt = (
                f"Pay {spec.life_payment} life to prevent the stack item "
                "from being countered."
            )
        else:
            rows = context.query.objects(
                zones=("hand",), owner=context.actor
            )
            legal_objects = tuple(
                FrozenMap(
                    {
                        "ref": row.ref,
                        "logical_object_id": row.logical_object_id,
                    }
                )
                for row in rows
            )
            continuation["_legal_objects"] = legal_objects
            payable = bool(rows)
            public["payable"] = payable
            if payable:
                choice = ObjectChoice(
                    field_name="cards",
                    legal_refs=tuple(row.ref for row in rows),
                    zones=("hand",),
                    minimum=0,
                    maximum=1,
                    optional=True,
                    visibility="actor_private",
                    owner_relation="actor",
                    schema_extras=FrozenMap(
                        {
                            "cards": [
                                {"id": row.ref, "name": row.printed_name}
                                for row in rows
                            ]
                        }
                    ),
                )
                prompt = (
                    "Discard one card to prevent the stack item from being "
                    "countered, or choose none to decline."
                )
            else:
                choice = ScalarChoice(
                    field_name="pay", legal_values=(False,)
                )
                prompt = "The discard-one Ward cost cannot be paid."
        return SemanticChoicePreparation(
            request=SemanticChoiceRequest(
                prompt=prompt,
                choice=choice,
                public_context=FrozenMap(public),
            ),
            continuation_effect=FrozenMap(continuation),
        )

    @staticmethod
    def _counter_completion(
        effect: Mapping[str, Any],
        *,
        actor: str,
        query: SemanticChoiceQuery,
        label: str,
    ) -> SemanticChoiceCompletion:
        target = str(effect.get("stack") or "")
        if query.stack_object(target) is None:
            return SemanticChoiceCompletion()
        return SemanticChoiceCompletion(
            intents=(
                CounterStackIntent(
                    actor=actor,
                    stack_ref=target,
                    reason=label,
                    countered_by=actor,
                ),
            )
        )

    def complete(
        self,
        continuation: SemanticChoiceContinuation,
        response: Mapping[str, Any],
        query: SemanticChoiceQuery,
    ) -> SemanticChoiceCompletion:
        effect = continuation.effect
        actor = str(effect.get("_choice_actor") or "")
        if not actor or actor not in query.active_seats:
            raise SemanticChoiceError("Ward continuation actor is malformed")
        spec = self._spec(effect)
        label = str(effect.get("_stack_label") or "Ward payment")
        target = str(effect.get("stack") or "")
        if query.stack_object(target) is None:
            return SemanticChoiceCompletion()
        if spec.generic_cost is not None:
            requirements = _strict_fixed_mana_requirements(
                effect.get("_requirements"),
                label="Ward",
                require_positive=False,
            )
            pay = _payment_choice(response)
            if pay and not query.cost_is_affordable(actor, requirements):
                raise SemanticChoiceError("The Ward mana cost is no longer payable")
            if pay:
                return SemanticChoiceCompletion(
                    intents=(
                        PayManaCostIntent(
                            actor=actor,
                            player=actor,
                            requirements=FrozenMap(requirements),
                            reason=label,
                            event_code="ward.paid",
                            message=f"{actor} paid Ward for {target}.",
                            details=FrozenMap(
                                {"cost": requirements, "stack": target}
                            ),
                        ),
                    )
                )
            return self._counter_completion(
                effect, actor=actor, query=query, label=label
            )
        if spec.life_payment is not None:
            amount = effect.get("_life_payment")
            if type(amount) is not int or amount != spec.life_payment:
                raise SemanticChoiceError("Ward life payment is malformed")
            pay = _payment_choice(response)
            if pay and query.player_life(actor) < amount:
                raise SemanticChoiceError("The Ward life cost is no longer payable")
            if pay:
                return SemanticChoiceCompletion(
                    intents=(
                        PayLifeIntent(
                            actor=actor,
                            player=actor,
                            amount=amount,
                            reason=label,
                        ),
                    )
                )
            return self._counter_completion(
                effect, actor=actor, query=query, label=label
            )
        selected = tuple(str(value) for value in response.get("cards", ()))
        if len(selected) > 1 or len(selected) != len(set(selected)):
            raise SemanticChoiceError("Ward discard selection is malformed")
        legal = {
            str(value["ref"]): str(value["logical_object_id"])
            for value in effect.get("_legal_objects", ())
        }
        if any(ref not in legal for ref in selected):
            raise SemanticChoiceError("Ward discard selection is not authoritative")
        if not selected:
            if "pay" in response and _payment_choice(response):
                raise SemanticChoiceError("Ward discard payment requires one card")
            return self._counter_completion(
                effect, actor=actor, query=query, label=label
            )
        card = query.object(selected[0], zones=("hand",))
        if (
            card is None
            or card.owner != actor
            or card.logical_object_id != legal[selected[0]]
        ):
            raise SemanticChoiceError("The Ward discard card is no longer payable")
        return SemanticChoiceCompletion(
            intents=(
                MoveObjectsSimultaneouslyIntent(
                    actor=actor,
                    object_refs=selected,
                    expected_zones=("hand",),
                    destination="graveyard",
                    reason=label,
                    transition_kind=ZoneTransitionKind.DISCARD,
                    owned_only=True,
                ),
            )
        )


PAYMENT_CHOICE_HANDLERS = (
    WardPaymentChoiceHandler(),
    OptionalPaymentHandler(
        operation=OPTIONAL_MANA_PAYMENT_OPERATION,
        handler_id="choice.payment.optional-fixed-effect.v1",
        mode="effect",
        prompt="Pay the stated cost to apply the triggered effect?",
        default_cost=FrozenMap(),
        capability_dependencies=(
            "effect.choice.optional_fixed_mana_payment",
        ),
        continuation_fields=(
            "player",
            "cost",
            "effects",
            "_choice_actor",
            "_requirements",
            "_source_ref",
            "_source_logical_object_id",
            "_stack_label",
        ),
        test_modules=(
            "tests.test_fixed_optional_mana_payment_triggers",
        ),
    ),
    OptionalPaymentHandler(
        operation="counter_unless_pay",
        handler_id="choice.payment.counter-unless.v1",
        mode="counter",
        prompt="Pay the stated cost to prevent the spell from being countered.",
        default_cost=FrozenMap(),
    ),
    OptionalPaymentHandler(
        operation="cumulative_upkeep",
        handler_id="choice.payment.cumulative-upkeep.v1",
        mode="cumulative",
        prompt="Pay cumulative upkeep or sacrifice the permanent.",
        default_cost=FrozenMap({"GENERIC": 1}),
        capability_dependencies=(
            "counter.producer.cumulative_upkeep_fixed_mana",
        ),
        continuation_fields=(
            "player",
            "cost_per_counter",
            "source",
            "stage",
            "_choice_actor",
            "_requirements",
            "_source_ref",
            "_source_logical_object_id",
            "_stack_label",
        ),
        projected_fields=(
            "prompt",
            "cost",
            "age_counters",
            "payable",
            "legal_actions.choice_schema.legal_values",
        ),
    ),
    OptionalPaymentHandler(
        operation="cumulative_upkeep_life",
        handler_id="choice.payment.cumulative-upkeep-life.v1",
        mode="cumulative_life",
        prompt="Pay fixed-life cumulative upkeep or sacrifice the permanent.",
        default_cost=FrozenMap(),
        capability_dependencies=(
            "counter.producer.cumulative_upkeep_fixed_life",
        ),
        rule_references=("CR 118.4", "CR 118.12", "CR 702.24"),
        private_data=("actor current life total",),
        continuation_fields=(
            "player",
            "life_per_counter",
            "source",
            "stage",
            "_choice_actor",
            "_life_payment",
            "_source_ref",
            "_source_logical_object_id",
            "_stack_label",
        ),
        projected_fields=(
            "prompt",
            "life_cost",
            "age_counters",
            "payable",
            "legal_actions.choice_schema.legal_values",
        ),
        mutation_path=("PayLifeIntent", "typed decline intent"),
        test_modules=(
            "tests.test_cumulative_upkeep_counter_placement",
        ),
    ),
    OptionalPaymentHandler(
        operation="echo_upkeep",
        handler_id="choice.payment.echo.v1",
        mode="echo",
        prompt="Pay Echo or sacrifice the permanent.",
        default_cost=FrozenMap(),
        capability_dependencies=("trigger.keyword.echo.fixed_mana",),
        rule_references=("CR 118.12", "CR 603.4", "CR 702.30"),
    ),
    OptionalPaymentHandler(
        operation="remora_tax",
        handler_id="choice.payment.remora-tax.v1",
        mode="remora",
        prompt=(
            "Pay {4} to prevent Mystic Remora's controller from drawing a card."
        ),
        default_cost=FrozenMap({"GENERIC": 4}),
    ),
    OptionalPaymentHandler(
        operation="pay_or_lose",
        handler_id="choice.payment-pay-or-lose.v1",
        mode="pact",
        prompt="Pay the delayed Pact cost or lose the game.",
        default_cost=FrozenMap(),
    ),
)
