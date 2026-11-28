from __future__ import annotations

"""Strict typed lowering for fixed counter-placement effects."""

from dataclasses import dataclass
from typing import Any, Mapping
from ..counter_names import EXISTING_COUNTER_AMOUNT, is_existing_counter_amount

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import (
    CounterPlacementAmount,
    IntentPlan,
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
)


from .all_kind_counters import AllKindCounterDoublingHandler

_REASON_FIELD = "reason"


def _placement_amount(value, *, allow_zero=False):
    if is_existing_counter_amount(value):
        return EXISTING_COUNTER_AMOUNT
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise SemanticNodeError("Counter placement amount must be an exact integer or a closed named-counter amount")
    return value


def _fixed_counter_group_intent(
    effect: Mapping[str, Any],
    context: ReadOnlyHandlerContext,
) -> PlaceCountersIntent:
    from ..rules.counter_marked_keep import COUNTER_NAME, KEEP_COUNTER_FIELD

    allowed = {
        "op",
        "cards",
        KEEP_COUNTER_FIELD,
        "counter",
        "amount",
        "source",
        _REASON_FIELD,
        "_replacement_selections",
    }
    unknown = sorted(set(effect) - allowed)
    if unknown:
        raise SemanticNodeError(
            "Counter placement group has unknown fields: "
            + ", ".join(unknown)
        )
    missing = sorted(
        {"op", "cards", "counter", "amount", "source"} - set(effect)
    )
    if missing:
        raise SemanticNodeError(
            "Counter placement group is missing fields: "
            + ", ".join(missing)
        )
    if effect.get("op") != "place_counters":
        raise SemanticNodeError("Counter placement group operation is unsupported")
    raw_cards = effect.get("cards")
    sealed = KEEP_COUNTER_FIELD in effect
    if sealed and (
        type(effect.get(KEEP_COUNTER_FIELD)) is not int or effect[KEEP_COUNTER_FIELD] != 1
        or effect.get("counter") != COUNTER_NAME or type(effect.get("amount")) is not int or effect["amount"] != 1
        or not isinstance(raw_cards, (list, tuple))
        or any(type(ref) is not str or not ref for ref in raw_cards) or len(set(raw_cards)) != len(raw_cards)
    ):
        raise SemanticNodeError("Counter-marked retention requires its distinct literal chosen recipients")
    maximum = len(context.query.active_seats) if sealed else 3
    if not isinstance(raw_cards, (list, tuple)) or not 2 <= len(raw_cards) <= maximum:
        raise SemanticNodeError("Counter placement group has an unsupported subject count")
    if any(
        value is not None and (type(value) is not str or not value)
        for value in raw_cards
    ):
        raise SemanticNodeError(
            "Counter placement group subjects must be public refs or unavailable"
        )
    counter_name = effect.get("counter")
    if type(counter_name) is not str or not counter_name.strip():
        raise SemanticNodeError(
            "Counter placement group requires one nonempty counter name"
        )
    amount = effect.get("amount")
    if type(amount) is not int or amount <= 0:
        raise SemanticNodeError(
            "Counter placement group amount must be a positive exact integer"
        )
    source_ref = effect.get("source")
    if type(source_ref) is not str or not source_ref:
        raise SemanticNodeError(
            "Counter placement group requires one nonempty source reference"
        )
    reason = effect.get(_REASON_FIELD, context.default_reason)
    if type(reason) is not str or not reason:
        raise SemanticNodeError("Counter placement group reason must be nonempty")
    raw_selections = effect.get("_replacement_selections", ())
    if raw_selections is None:
        raw_selections = ()
    if not isinstance(raw_selections, (list, tuple)):
        raise SemanticNodeError(
            "Counter placement group replacement selections must be an array"
        )
    try:
        return PlaceCountersIntent(
            actor=context.actor,
            object_refs=tuple(
                value for value in raw_cards if value is not None
            ),
            counter_name=" ".join(counter_name.casefold().split()),
            amount=amount,
            reason=reason,
            source_ref=source_ref,
            replacement_selections=tuple(raw_selections),
        )
    except (TypeError, ValueError) as exc:
        raise SemanticNodeError(str(exc)) from exc


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementHandler:
    handler_id: str = "generic.fixed-counter-placement.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement"
    operation: str = "place_counters"
    rule_references: tuple[str, ...] = (
        "115.3",
        "115.6",
        "122.1",
        "122.1a",
        "122.6",
        "608.2c",
        "608.2f",
        "608.2h",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..reflexive_trigger_model import SOURCE_COUNTER_REFLEXIVE_FIELD
        from ..rules.noted_mana import NOTED_MANA_EFFECT_FIELD, lower_noted_mana_charge

        if NOTED_MANA_EFFECT_FIELD in effect:
            return lower_noted_mana_charge(self, effect, context)

        if SOURCE_COUNTER_REFLEXIVE_FIELD in effect:
            from .source_counter_reflexive_intent import lower_source_counter_reflexive

            return lower_source_counter_reflexive(effect, context, handler_id=self.handler_id)
        if effect.get('card', '') is None:
            if 'cards' in effect:
                raise SemanticNodeError('Counter placement cannot combine single and group subjects')
            # Resolved source-incarnation references can be unavailable. The
            # existing group validator already validates and omits those rows.
            grouped = {key: value for key, value in effect.items() if key != 'card'}
            grouped['cards'] = [None, None]
            return self.lower(grouped, context)
        if "cards" in effect:
            return IntentPlan(
                operation=self.operation,
                handler_id=self.handler_id,
                intents=(_fixed_counter_group_intent(effect, context),),
            )
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Counter placement",
            allow_replacement_selections=True,
            additional_allowed_fields=("counter", "amount", "source"),
        )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter placement requires one nonempty counter name"
            )
        amount = _placement_amount(effect.get("amount"), allow_zero=True)
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter placement requires one nonempty source reference"
            )
        if amount == 0:
            return IntentPlan(
                operation=self.operation,
                handler_id=self.handler_id,
                intents=(),
                result_shape="by_player",
            )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                PlaceCountersIntent(
                    actor=context.actor,
                    object_refs=(fields.object_ref,),
                    counter_name=" ".join(counter_name.casefold().split()),
                    amount=amount,
                    reason=fields.reason,
                    source_ref=source_ref,
                    replacement_selections=fields.replacement_selections,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementBatchHandler:
    handler_id: str = "generic.fixed-counter-placement-batch.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement-batch"
    operation: str = "place_counter_batch"
    rule_references: tuple[str, ...] = (
        "122.1",
        "122.1a",
        "122.6",
        "608.2c",
        "608.2h",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_multikind_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..rules.death_counter_shuffle import DEATH_COUNTER_SHUFFLE_FIELD
        from ..counter_placement_sets import (
            SOURCE_TARGET_COUNTER_BATCH_CARD_FIELD, source_target_counter_batch_projection,
            uses_source_target_counter_batch,
        )

        from ..rules.departure_counter_copy import departure_counter_copy_intent, uses_departure_counter_copy

        if uses_departure_counter_copy(effect):
            intent = departure_counter_copy_intent(effect, context)
            return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(() if intent is None else (intent,)))
        paired_source = None
        if uses_source_target_counter_batch(effect):
            try:
                paired_source = effect[SOURCE_TARGET_COUNTER_BATCH_CARD_FIELD]
                effect = source_target_counter_batch_projection(effect, bound=True)
            except (TypeError, ValueError, KeyError) as exc:
                raise SemanticNodeError(str(exc)) from exc
        departure = DEATH_COUNTER_SHUFFLE_FIELD in effect
        if departure:
            marker = effect[DEATH_COUNTER_SHUFFLE_FIELD]
            if type(marker) is not int or marker != 1:
                raise SemanticNodeError("Unsupported departure counter-placement version")
            effect = {key: value for key, value in effect.items() if key != DEATH_COUNTER_SHUFFLE_FIELD}
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Counter batch placement",
            allow_replacement_selections=True,
            additional_allowed_fields=("placements", "source"),
        )
        raw_placements = effect.get("placements")
        if not isinstance(raw_placements, (list, tuple)) or (
            not departure and not 2 <= len(raw_placements) <= 3
        ):
            raise SemanticNodeError(
                "Counter batch placement requires two or three entries"
            )
        placements: list[CounterPlacementAmount] = []
        for index, raw in enumerate(raw_placements):
            if not isinstance(raw, Mapping) or set(raw) != {
                "counter",
                "amount",
            }:
                raise SemanticNodeError(
                    f"Counter batch placement entry {index} is malformed"
                )
            try:
                placements.append(
                    CounterPlacementAmount(
                        counter_name=raw.get("counter"),
                        amount=raw.get("amount"),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter batch placement requires one nonempty source reference"
            )
        if departure and not placements:
            if fields.replacement_selections:
                raise SemanticNodeError("An empty departure snapshot has no counter placement to replace")
            return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=())
        try:
            intent = PlaceCounterBatchIntent(
                actor=context.actor,
                object_ref=fields.object_ref,
                placements=tuple(placements),
                reason=fields.reason,
                source_ref=source_ref,
                replacement_selections=fields.replacement_selections,
                paired_source_ref=paired_source,
                source_departure=departure,
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementSetHandler:
    handler_id: str = "generic.fixed-counter-placement-set.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement-set"
    operation: str = "place_counters_on_set"
    rule_references: tuple[str, ...] = (
        "122.1",
        "122.1a",
        "122.6",
        "608.2c",
        "608.2h",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_permanent_set_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..counter_placement_sets import (
            PAIRED_PLANESWALKER_LOYALTY_FIELD, SOURCE_AND_CONTROLLED_SUBTYPE_FIELD,
            paired_planeswalker_loyalty_dependencies, source_and_controlled_subtype_dependencies,
        )

        from ..rules.attack_batches import (
            DECLARED_ATTACK_COUNTER_FIELD, declared_attack_counter_set, resolved_declared_attack_counter_effect,
        )

        allowed = {
            PAIRED_PLANESWALKER_LOYALTY_FIELD,
            SOURCE_AND_CONTROLLED_SUBTYPE_FIELD,
            DECLARED_ATTACK_COUNTER_FIELD,
            "op",
            "source",
            "set",
            "counter",
            "amount",
            _REASON_FIELD,
            "_replacement_selections",
        }
        unknown = sorted(set(effect) - allowed)
        if unknown:
            raise SemanticNodeError(
                "Counter-set effect has unknown fields: "
                + ", ".join(unknown)
            )
        missing = sorted(
            {"op", "source", "set", "counter", "amount"} - set(effect)
        )
        if missing:
            raise SemanticNodeError(
                "Counter-set effect is missing fields: "
                + ", ".join(missing)
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError("Counter-set operation is unsupported")
        try:
            if DECLARED_ATTACK_COUNTER_FIELD in effect:
                spec = declared_attack_counter_set()
            else:
                spec = AffectedPermanentSetSpec.from_dict(effect.get("set"))
        except (AffectedPermanentSetError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if spec.controller_relation is PermanentControllerRelation.TARGET_PLAYER:
            context.query.require_active_seat(
                str(spec.target_controller or "")
            )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter-set placement requires one nonempty counter name"
            )
        amount = _placement_amount(effect.get("amount"))
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter-set placement requires one nonempty source reference"
            )
        raw_reason = effect.get(_REASON_FIELD)
        if raw_reason is not None and (
            type(raw_reason) is not str or not raw_reason
        ):
            raise SemanticNodeError(
                "Counter-set reason must be a nonempty string"
            )
        raw_selections = effect.get("_replacement_selections")
        if raw_selections is None:
            raw_selections = ()
        if not isinstance(raw_selections, (list, tuple)):
            raise SemanticNodeError(
                "Counter-set replacement selections must be an array"
            )
        try:
            paired_loyalty_source = None
            source_and_subtype_identity = None
            declared_attack_members = None
            if DECLARED_ATTACK_COUNTER_FIELD in effect:
                instruction = {key: value for key, value in effect.items()
                               if key not in {_REASON_FIELD, "_replacement_selections"}}
                declared_attack_members = resolved_declared_attack_counter_effect(instruction, source_ref=source_ref)
            if SOURCE_AND_CONTROLLED_SUBTYPE_FIELD in effect:
                instruction = {key: value for key, value in effect.items()
                               if key not in {_REASON_FIELD, "_replacement_selections"}}
                source_and_controlled_subtype_dependencies(instruction, source_ref=source_ref)
                original = context.source
                if original is None or original.card_ref != source_ref or original.object_id is None:
                    raise ValueError("Source and subtype counters lost the original resolving source identity")
                source_and_subtype_identity = (original.object_id, original.logical_object_id)
            if PAIRED_PLANESWALKER_LOYALTY_FIELD in effect:
                instruction = {key: value for key, value in effect.items()
                               if key not in {_REASON_FIELD, "_replacement_selections"}}
                paired_planeswalker_loyalty_dependencies(instruction, source_ref=source_ref)
                original = context.source
                if original is None or original.card_ref != source_ref or original.object_id is None:
                    raise ValueError("Paired loyalty lost its original resolving source identity")
                paired_loyalty_source = (original.object_id, original.logical_object_id)
            intent = PlaceCountersOnSetIntent(
                actor=context.actor,
                spec=spec,
                counter_name=counter_name,
                amount=amount,
                reason=raw_reason or context.default_reason,
                source_ref=source_ref,
                replacement_selections=tuple(raw_selections),
                paired_loyalty_source=paired_loyalty_source,
                source_and_subtype_identity=source_and_subtype_identity,
                declared_attack_members=declared_attack_members,
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementTargetSetHandler:
    handler_id: str = "generic.fixed-counter-placement-target-set.v1"
    schema_version: int = 1
    family: str = "effect.counter-placement-target-set"
    operation: str = "place_counters_on_targets"
    rule_references: tuple[str, ...] = (
        "115.1",
        "115.3",
        "115.6",
        "122.1",
        "122.1a",
        "122.6",
        "608.2b",
        "614.16",
        "616.1",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_permanent_target_set_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        versioned='schema_version' in effect
        if versioned:
            from ..rules.counter_target_set_shapes import validate_quantified_counter_target_effect
            try:variable,_relative=validate_quantified_counter_target_effect(effect,resolved=True)
            except (ValueError,TypeError) as exc:raise SemanticNodeError(str(exc)) from exc
        else:variable=False
        allowed = {
            "op",
            "cards",
            "maximum_targets",
            "counter",
            "amount",
            "source",
            _REASON_FIELD,
            "_replacement_selections",
        }
        if versioned:allowed.update(('schema_version','announced_target_count'))
        unknown = sorted(set(effect) - allowed)
        if unknown:
            raise SemanticNodeError(
                "Counter-target effect has unknown fields: "
                + ", ".join(unknown)
            )
        missing = sorted(
            {
                "op",
                "cards",
                "maximum_targets",
                "counter",
                "amount",
                "source",
            }
            - set(effect)
        )
        if missing:
            raise SemanticNodeError(
                "Counter-target effect is missing fields: "
                + ", ".join(missing)
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError("Counter-target operation is unsupported")
        raw_cards = effect.get("cards")
        if not isinstance(raw_cards, (list, tuple)):
            raise SemanticNodeError(
                "Counter-target placement requires an array of targets"
            )
        cards = tuple(raw_cards)
        maximum = effect.get("maximum_targets")
        if (
            type(maximum) is not int
            or (maximum < 0 if variable else maximum <= 0)
            or len(cards) > maximum
            or any(type(card) is not str or not card for card in cards)
            or len(cards) != len(set(cards))
        ):
            raise SemanticNodeError(
                "Counter-target placement requires unique targets within a positive maximum"
            )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Counter-target placement requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if versioned:amount=_placement_amount(amount)
        if ((not versioned or amount != EXISTING_COUNTER_AMOUNT)
                and (type(amount) is not int or amount <= 0)):
            raise SemanticNodeError(
                "Counter-target amount must be a positive exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Counter-target placement requires one nonempty source reference"
            )
        raw_reason = effect.get(_REASON_FIELD)
        if raw_reason is not None and (
            type(raw_reason) is not str or not raw_reason
        ):
            raise SemanticNodeError(
                "Counter-target reason must be a nonempty string"
            )
        raw_selections = effect.get("_replacement_selections")
        if raw_selections is None:
            raw_selections = ()
        if not isinstance(raw_selections, (list, tuple)):
            raise SemanticNodeError(
                "Counter-target replacement selections must be an array"
            )
        if versioned and not cards:
            return IntentPlan(operation=self.operation,handler_id=self.handler_id,intents=(),result_shape='by_player')
        try:
            intent = PlaceCountersOnTargetsIntent(
                actor=context.actor,
                object_refs=cards,
                maximum_targets=maximum,
                counter_name=counter_name,
                amount=amount,
                reason=raw_reason or context.default_reason,
                source_ref=source_ref,
                replacement_selections=tuple(raw_selections),
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


@dataclass(frozen=True, slots=True)
class FixedPlayerCounterPlacementHandler:
    handler_id: str = "generic.fixed-player-counter-placement.v1"
    schema_version: int = 1
    family: str = "effect.player-counter-placement"
    operation: str = "place_player_counters"
    rule_references: tuple[str, ...] = (
        "101.4",
        "107.14",
        "107.17",
        "115.1",
        "122.1",
        "608.2b",
        "608.2c",
    )
    capability_dependencies: tuple[str, ...] = (
        "counter.producer.fixed_player_effect",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        subject = effect.get("subjects")
        base_fields = {
            "op",
            "subjects",
            "counter",
            "amount",
            "source",
            _REASON_FIELD,
            "_replacement_selections",
        }
        allowed = base_fields | ({"target"} if subject == "target" else set())
        from ..rad_counter_model import RAD_PRODUCER_FIELD, versioned_rad_player_counter_effect

        try:
            if versioned_rad_player_counter_effect(effect):
                allowed.add(RAD_PRODUCER_FIELD)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if 'zero_is_noop' in effect:
            if effect['zero_is_noop'] is not True or subject != 'controller' or effect.get('counter') != 'energy':
                raise SemanticNodeError('Only counted controller energy permits a zero result')
            allowed.add('zero_is_noop')
        unknown = sorted(set(effect) - allowed)
        if unknown:
            raise SemanticNodeError(
                "Player counter effect has unknown fields: "
                + ", ".join(unknown)
            )
        if effect.get("op") != self.operation or subject not in {
            "controller",
            "target",
            "each-player",
            "each-opponent",
        }:
            raise SemanticNodeError(
                "Player counter placement subject is unsupported"
            )
        counter_name = effect.get("counter")
        if type(counter_name) is not str or not counter_name.strip():
            raise SemanticNodeError(
                "Player counter placement requires one nonempty counter name"
            )
        amount = effect.get("amount")
        if type(amount) is not int or amount < 0 or (amount == 0 and 'zero_is_noop' not in effect):
            raise SemanticNodeError(
                "Player counter amount must be a positive exact integer"
            )
        source_ref = effect.get("source")
        if type(source_ref) is not str or not source_ref:
            raise SemanticNodeError(
                "Player counter placement requires one nonempty source reference"
            )
        reason = effect.get(_REASON_FIELD, context.default_reason)
        if type(reason) is not str or not reason:
            raise SemanticNodeError(
                "Player counter placement reason must be nonempty"
            )
        raw_selections = effect.get("_replacement_selections", ())
        if raw_selections is None:
            raw_selections = ()
        if not isinstance(raw_selections, (list, tuple)):
            raise SemanticNodeError(
                "Player counter replacement selections must be an array"
            )
        if subject == "controller":
            players = (context.query.require_active_seat(context.actor),)
        elif subject == "target":
            target = effect.get("target")
            if type(target) is not str or not target:
                raise SemanticNodeError(
                    "Targeted player counter placement requires one target"
                )
            players = (context.query.require_active_seat(target),)
        elif subject == "each-player":
            players = context.query.apnap_order
        else:
            players = tuple(
                player
                for player in context.query.apnap_order
                if player != context.actor
            )
        if amount == 0:
            if raw_selections:
                raise SemanticNodeError('Zero energy placement cannot select a replacement')
            return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(), result_shape='by_player')
        try:
            intent = PlacePlayerCountersIntent(
                actor=context.actor,
                player_ids=players,
                counter_name=counter_name,
                amount=amount,
                reason=reason,
                source_ref=source_ref,
                replacement_selections=tuple(raw_selections),
            )
        except (TypeError, ValueError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


COUNTER_PLACEMENT_HANDLERS = (
    FixedCounterPlacementHandler(),
    FixedCounterPlacementBatchHandler(),
    FixedCounterPlacementSetHandler(),
    FixedCounterPlacementTargetSetHandler(),
    FixedPlayerCounterPlacementHandler(),
    AllKindCounterDoublingHandler(),
)


__all__ = [
    "COUNTER_PLACEMENT_HANDLERS",
    "FixedCounterPlacementHandler",
    "FixedCounterPlacementBatchHandler",
    "FixedCounterPlacementSetHandler",
    "FixedCounterPlacementTargetSetHandler",
    "FixedPlayerCounterPlacementHandler",
]
