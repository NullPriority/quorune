from __future__ import annotations

"""Closed runtime owners for homogeneous fixed target-set effects."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..rules.announced_destruction_controller_tokens import AnnouncedDestructionControllerTokens
from ..rules.controlled_destruction_source_counters import decode_announced_destruction_followup
from ..rules.graveyard_card_targets import OPPOSING_GRAVEYARD_RETURN_CAPABILITY, OPPOSING_GRAVEYARD_RETURN_OPERATION
from ..tap_state import TAP_TARGET_SET_UNTAP_FIELD
from ..rules.compound_target_sets import OPTIONAL_DESTROY_ROLES_VERSION_FIELD, optional_destroy_roles_payload_is_closed
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .zone_move_intent import FrontFaceGraveyardCreatureReturnIntent
from .intents import (
    DestroyPermanentTargetsIntent,
    IntentPlan,
    MoveObjectsSimultaneouslyIntent,
    SetPermanentsTappedIntent,
)


_COMMON_FIELDS = frozenset(
    {
        "op",
        "cards",
        "maximum_targets",
        "reason",
        "_replacement_selections",
        "announced_target_count",
    }
)
_FIXED_TARGET_SET_CAPABILITY = "resolution.effect.fixed_homogeneous_target_set"


def bind_revalidated_tap_target_cards(item, raw_effect, effect):
    """Drop only slots invalidated by the stack target revalidator."""
    if effect.get("op") not in {"tap_targets", "untap_targets"} or item.context.get("targets_revalidated") is not True:
        return effect
    cards = effect.get("cards")
    if not isinstance(cards, (list, tuple)) or None not in cards:
        return effect
    refs = raw_effect.get("cards")
    if not isinstance(refs, (list, tuple)) or len(refs) != len(cards):
        return effect
    stale_refs = {f"$target.{index}" for index, target in enumerate(item.targets) if target is None}
    if any(type(ref) is not str or ref not in stale_refs for ref, card in zip(refs, cards, strict=True) if card is None):
        return effect
    return {**effect, "cards": [card for card in cards if card is not None]}


def _fixed_target_fields(
    effect: Mapping[str, Any],
    context: ReadOnlyHandlerContext,
    *,
    operation: str,
    replacements: bool,
    additional_fields: tuple[str,...] = (),
    variable_maximum: bool = False,
) -> tuple[tuple[str, ...], int, str, tuple[Any, ...]]:
    extra = set(additional_fields)
    if 'target_roles' in effect:
        if (effect['target_roles'] is not True or 'announced_target_count' in effect
                or operation not in {'destroy_targets','exile_permanent_targets','return_permanent_targets_to_owner_hand'}):
            raise SemanticNodeError('Target roles require an owned simultaneous operation')
        extra.add('target_roles')
    if OPTIONAL_DESTROY_ROLES_VERSION_FIELD in effect:
        if operation != 'destroy_targets' or not optional_destroy_roles_payload_is_closed(effect):
            raise SemanticNodeError('Optional destruction lost its exact two-role payload')
        extra.add(OPTIONAL_DESTROY_ROLES_VERSION_FIELD)
    if operation == 'destroy_targets' and 'regeneration_prohibited' in effect:
        if effect['regeneration_prohibited'] is not True:raise SemanticNodeError('Regeneration prohibition must be true')
        extra.add('regeneration_prohibited')
    unknown = sorted(set(effect) - _COMMON_FIELDS - extra)
    if unknown:
        raise SemanticNodeError(
            "Fixed target-set effect has unknown fields: " + ", ".join(unknown)
        )
    if effect.get("op") != operation:
        raise SemanticNodeError("Fixed target-set operation is unsupported")
    raw_cards = effect.get("cards")
    maximum = effect.get("maximum_targets")
    variable = effect.get("announced_target_count") is True
    if "announced_target_count" in effect and not variable:
        raise SemanticNodeError("Announced target-count selector must be true")
    if (
        not isinstance(raw_cards, (list, tuple))
        or any(type(value) is not str or not value for value in raw_cards)
        or 'target_roles' not in effect and len(raw_cards) != len(set(raw_cards))
        or type(maximum) is not int
        or not (maximum >= 0 if variable or variable_maximum else 1 <= maximum <= 6)
        or len(raw_cards) > maximum
    ):
        raise SemanticNodeError(
            "Fixed target-set effects require at most their unique target bound"
        )
    if 'target_roles' in effect:
        if not 2 <= maximum <= 4:raise SemanticNodeError('Target role bound is unsupported')
        raw_cards = tuple(dict.fromkeys(raw_cards))
    raw_reason = effect.get("reason")
    if raw_reason is not None and (
        type(raw_reason) is not str or not raw_reason
    ):
        raise SemanticNodeError(
            "Fixed target-set reason must be a nonempty string"
        )
    raw_selections = effect.get("_replacement_selections", ())
    if not isinstance(raw_selections, (list, tuple)):
        raise SemanticNodeError(
            "Fixed target-set replacement selections must be an array"
        )
    if raw_selections and not replacements:
        raise SemanticNodeError(
            "Tap-state target sets do not accept replacement selections"
        )
    return (
        tuple(raw_cards),
        maximum,
        raw_reason or context.default_reason,
        tuple(raw_selections),
    )


def _target_set_plan(
    operation: str, handler_id: str, cards: tuple[str, ...], intent: Any,
) -> IntentPlan:
    """The intent is constructed and validated even when there is no work."""
    return IntentPlan(
        operation=operation,
        handler_id=handler_id,
        intents=(intent,) if cards else (),
        result_shape="single" if cards else "by_player",
    )


@dataclass(frozen=True, slots=True)
class DestroyPermanentTargetSetHandler:
    handler_id: str = "generic.destroy-permanent-target-set.v1"
    schema_version: int = 1
    family: str = "effect.permanent-destruction-target-set"
    operation: str = "destroy_targets"
    rule_references: tuple[str, ...] = (
        "608.2b",
        "608.2c",
        "701.8",
        "701.8a",
        "701.8b",
        "701.8c",
        "702.12b",
    )
    capability_dependencies: tuple[str, ...] = (
        "permanent.destroy.effect",
        _FIXED_TARGET_SET_CAPABILITY,
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        cards, _maximum, reason, selections = _fixed_target_fields(
            effect,
            context,
            operation=self.operation,
            replacements=True,
            additional_fields=("followup",),
        )
        followup = None
        if "followup" in effect:
            try:
                followup = decode_announced_destruction_followup(effect["followup"], resolved=True)
                variable = effect.get("announced_target_count") is True
                graveyard = isinstance(followup, AnnouncedDestructionControllerTokens) and followup.requires_graveyard
                valid_bound = not variable and _maximum == 3 if graveyard else variable
                if not valid_bound or "target_roles" in effect or "regeneration_prohibited" in effect:
                    raise ValueError("Controller tokens require their original ordinary destruction target bound")
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
        return _target_set_plan(
            self.operation, self.handler_id, cards,
            DestroyPermanentTargetsIntent(
                actor=context.actor,
                object_refs=cards,
                reason=reason,
                replacement_selections=selections,
                regeneration_prohibited=effect.get('regeneration_prohibited',False),
                followup=followup,
            ),
        )


@dataclass(frozen=True, slots=True)
class MoveFixedTargetSetHandler:
    handler_id: str
    family: str
    operation: str
    expected_zone: str
    destination: str
    owned_only: bool
    capability_dependencies: tuple[str, ...]
    schema_version: int = 1
    rule_references: tuple[str, ...] = (
        "400.2",
        "400.3",
        "400.7",
        "608.2b",
        "608.2c",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        from ..linked_exile_activation_model import LINKED_ACTIVATION_BINDING, LINKED_ACTIVATION_PAIR

        if self.operation == "exile_public_graveyard_targets" and (
            LINKED_ACTIVATION_BINDING in effect or LINKED_ACTIVATION_PAIR in effect
        ):
            from .linked_exile_graveyard import lower_linked_graveyard_exile

            return lower_linked_graveyard_exile(self, effect, context)
        if self.operation == 'exile_public_graveyard_targets' and 'followup' in effect:
            from .targeted_graveyard_outcome_handlers import lower_targeted_graveyard_outcome

            return lower_targeted_graveyard_outcome(effect, context, handler_id=self.handler_id)
        from ..rules.opponent_exile_draw_results import OPPONENT_EXILE_DRAW_FIELD
        from ..rules.affected_library_search_batch import EXILE_LAND_SEARCH_FIELD

        exile_tokens = self.operation == "exile_permanent_targets" and "followup" in effect
        exile_draw = self.operation == "exile_permanent_targets" and OPPONENT_EXILE_DRAW_FIELD in effect
        exile_search = self.operation == "exile_permanent_targets" and EXILE_LAND_SEARCH_FIELD in effect
        cards, _maximum, reason, selections = _fixed_target_fields(
            effect,
            context,
            operation=self.operation,
            replacements=True,
            additional_fields=(("followup",) if exile_tokens else (OPPONENT_EXILE_DRAW_FIELD,) if exile_draw
                               else (EXILE_LAND_SEARCH_FIELD,) if exile_search else ()),
        )
        intent_type = MoveObjectsSimultaneouslyIntent
        options = {}
        if exile_tokens:
            from ..rules.targeted_exile_token_results import TargetedExileTokenFollowup
            from .targeted_exile_token_intent import TargetedExileTokensIntent

            try:
                options["followup"] = TargetedExileTokenFollowup.from_dict(effect["followup"], actor=context.actor)
            except (TypeError, ValueError) as exc:
                raise SemanticNodeError(str(exc)) from exc
            intent_type = TargetedExileTokensIntent
        elif exile_draw:
            from .targeted_exile_token_intent import OpponentExileDrawIntent

            version = effect[OPPONENT_EXILE_DRAW_FIELD]
            if type(version) is not int or version != 1 or effect.get("announced_target_count") is not True:
                raise SemanticNodeError("Opponent exile draws require the closed announced instruction")
            intent_type = OpponentExileDrawIntent
        elif exile_search:
            from .targeted_exile_token_intent import ExileControllerLandSearchIntent

            marker = effect[EXILE_LAND_SEARCH_FIELD]
            if type(marker) is not int or marker != 1 or _maximum != 1:
                raise SemanticNodeError("Compensation requires its original single creature target")
            intent_type = ExileControllerLandSearchIntent
        return _target_set_plan(
            self.operation, self.handler_id, cards,
            intent_type(
                actor=context.actor,
                object_refs=cards,
                expected_zones=(self.expected_zone,),
                destination=self.destination,
                reason=reason,
                owned_only=self.owned_only,
                replacement_selections=selections,
                **options,
            ),
        )


@dataclass(frozen=True, slots=True)
class ReanimateFixedTargetSetHandler:
    handler_id: str = 'generic.reanimate-target-set.v1'
    family: str = 'effect.graveyard-reanimation-target-set'
    operation: str = 'reanimate_targets'
    schema_version: int = 1
    capability_dependencies: tuple[str,...] = ('zone.reanimate.fixed_target_set',
        'zone.reanimate.fixed_target',_FIXED_TARGET_SET_CAPABILITY)
    rule_references: tuple[str,...] = ('400.6','400.7','603.6a','608.2b','614.12','614.12a')

    def lower(self,effect:Mapping[str,Any],context:ReadOnlyHandlerContext)->IntentPlan:
        from ..public_zone_moves import PublicBattlefieldController
        from ..rules.announced_mana_value_target import DISTINCT_REANIMATION_RESULT_VERSION
        from ..rules.reanimation_followup import RETURNED_CREATURE_COUNTER_VERSION
        from .zone_move_intent import ReturnedCreatureCounterIntent

        version = effect.get("schema_version")
        returned_counters = type(version) is int and version == RETURNED_CREATURE_COUNTER_VERSION
        announced = version == DISTINCT_REANIMATION_RESULT_VERSION
        if (
            type(version) is not int or version not in (1, DISTINCT_REANIMATION_RESULT_VERSION, RETURNED_CREATURE_COUNTER_VERSION)
            or type(effect.get("battlefield_tapped")) is not bool
            or announced != ("announced_target_count" in effect)
            or announced and (effect["announced_target_count"] is not True
                              or effect.get("battlefield_controller") != "actor" or effect["battlefield_tapped"])
        ):
            raise SemanticNodeError("Reanimation target sets require their closed entry instruction")
        try:controller=PublicBattlefieldController(effect.get('battlefield_controller'))
        except (ValueError,TypeError) as exc:raise SemanticNodeError('Reanimation entry controller is unsupported') from exc
        cards,maximum,reason,selections=_fixed_target_fields(effect,context,operation=self.operation,
            replacements=True,additional_fields=('schema_version','battlefield_controller','battlefield_tapped'))
        if not announced and maximum < 2:
            raise SemanticNodeError("One-target reanimation retains its scalar owner")
        context.query.require_known_seat(context.actor)
        if returned_counters:
            if (maximum != 2 or controller is not PublicBattlefieldController.ACTOR
                    or effect["battlefield_tapped"] or context.source is None):
                raise SemanticNodeError("Returned creature counters require their original owned reanimation")
            return _target_set_plan(self.operation, self.handler_id, cards, ReturnedCreatureCounterIntent(
                actor=context.actor, object_refs=cards, expected_zones=("graveyard",), destination="battlefield",
                reason=reason, owned_only=True, replacement_selections=selections, battlefield_controller=controller,
                source_ref=context.source.card_ref or context.source.stack_ref,
            ))
        intent = FrontFaceGraveyardCreatureReturnIntent if announced else MoveObjectsSimultaneouslyIntent
        return _target_set_plan(self.operation,self.handler_id,cards,intent(
            actor=context.actor,object_refs=cards,expected_zones=('graveyard',),destination='battlefield',reason=reason,
            replacement_selections=selections,battlefield_controller=controller,
            battlefield_tapped=effect['battlefield_tapped'],owned_only=announced))


@dataclass(frozen=True, slots=True)
class SetFixedTargetSetTappedHandler:
    handler_id: str
    operation: str
    tapped: bool
    capability_dependencies: tuple[str, ...]
    schema_version: int = 1
    family: str = "effect.permanent-tap-state-target-set"
    rule_references: tuple[str, ...] = (
        "608.2b",
        "608.2c",
        "701.26",
        "701.26a",
        "701.26b",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        cards, _maximum, reason, _selections = _fixed_target_fields(
            effect,
            context,
            operation=self.operation,
            replacements=False,
            additional_fields=(TAP_TARGET_SET_UNTAP_FIELD,) if self.tapped else (),
        )
        prohibit = TAP_TARGET_SET_UNTAP_FIELD in effect
        if prohibit and effect[TAP_TARGET_SET_UNTAP_FIELD] is not True:
            raise SemanticNodeError("The next-untap instruction must be true")
        return _target_set_plan(
            self.operation, self.handler_id, cards,
            SetPermanentsTappedIntent(
                object_refs=cards,
                actor=context.actor,
                tapped=self.tapped,
                reason=reason,
                prohibit_next_untap=prohibit,
            ),
        )


FIXED_TARGET_SET_HANDLERS = (
    ReanimateFixedTargetSetHandler(),
    DestroyPermanentTargetSetHandler(),
    MoveFixedTargetSetHandler(
        handler_id="generic.exile-permanent-target-set.v1",
        family="effect.permanent-exile-target-set",
        operation="exile_permanent_targets",
        expected_zone="battlefield",
        destination="exile",
        owned_only=False,
        capability_dependencies=(
            "permanent.exile.effect",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.return-permanent-target-set.v1",
        family="effect.permanent-return-target-set",
        operation="return_permanent_targets_to_owner_hand",
        expected_zone="battlefield",
        destination="hand",
        owned_only=False,
        capability_dependencies=(
            "permanent.return.owner_hand",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.return-graveyard-target-set.v1",
        family="effect.graveyard-card-return-target-set",
        operation="return_graveyard_targets_to_owner_hand",
        expected_zone="graveyard",
        destination="hand",
        owned_only=True,
        capability_dependencies=(
            "card.return.own_graveyard_to_owner_hand",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.return-opposing-graveyard-target-set.v1",
        family="effect.graveyard-card-return-target-set",
        operation=OPPOSING_GRAVEYARD_RETURN_OPERATION,
        expected_zone="graveyard",
        destination="hand",
        owned_only=False,
        capability_dependencies=(OPPOSING_GRAVEYARD_RETURN_CAPABILITY,),
    ),
    MoveFixedTargetSetHandler(
        handler_id="generic.exile-public-graveyard-target-set.v1",
        family="effect.public-graveyard-exile-target-set",
        operation="exile_public_graveyard_targets",
        expected_zone="graveyard",
        destination="exile",
        owned_only=False,
        capability_dependencies=(
            "card.exile.public_graveyard",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    SetFixedTargetSetTappedHandler(
        handler_id="generic.tap-target-set.v1",
        operation="tap_targets",
        tapped=True,
        capability_dependencies=(
            "permanent.tap.effect",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
    SetFixedTargetSetTappedHandler(
        handler_id="generic.untap-target-set.v1",
        operation="untap_targets",
        tapped=False,
        capability_dependencies=(
            "permanent.untap.effect",
            _FIXED_TARGET_SET_CAPABILITY,
        ),
    ),
)


__all__ = [
    "DestroyPermanentTargetSetHandler",
    "FIXED_TARGET_SET_HANDLERS",
    "MoveFixedTargetSetHandler",
    "ReanimateFixedTargetSetHandler",
    "SetFixedTargetSetTappedHandler",
]
