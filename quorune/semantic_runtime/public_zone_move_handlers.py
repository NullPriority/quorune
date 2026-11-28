from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..public_zone_moves import (
    PublicZoneMoveError,
    PublicZoneMoveSetSpec,
    PublicZoneSeatRelation,
)
from ..rules.linked_clue_copy import (
    LINKED_CLUE_COPY_VERSION, has_linked_clue_copy_version, linked_clue_copy_source_identity,
    require_linked_clue_copy_version, validate_linked_clue_pair,
)
from ..rules.public_exile_result_model import PublicExileFollowup
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .direct_target_fields import validate_direct_target_effect
from .intents import IntentPlan
from .zone_move_intent import ExilePublicGraveyardCardIntent, MovePublicZoneSetIntent
from .graveyard_card_result_handlers import GraveyardCardCounterLifeHandler
from .graveyard_exile_branch_handlers import GraveyardExileBranchHandler
from .graveyard_exile_token_handlers import GraveyardExileTokenHandler
from .graveyard_exile_type_outcome_handlers import GraveyardExileTypeOutcomeHandler


@dataclass(frozen=True, slots=True)
class ExilePublicGraveyardCardHandler:
    handler_id: str = "generic.exile-public-graveyard-card.v1"
    schema_version: int = 1
    family: str = "effect.public-zone-move"
    operation: str = "exile_public_graveyard_card"
    rule_references: tuple[str, ...] = (
        "400.2",
        "400.7",
        "406.1",
        "406.2",
        "608.2b",
        "608.2c",
        "701.13a",
    )
    capability_dependencies: tuple[str, ...] = (
        "card.exile.public_graveyard",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        linked_source = None
        linked_pair = None
        if has_linked_clue_copy_version(effect):
            try:
                require_linked_clue_copy_version(effect)
                linked_source = linked_clue_copy_source_identity(context.source)
                linked_pair = validate_linked_clue_pair(effect.get("ability_pair"))
            except ValueError as exc:
                raise SemanticNodeError(str(exc)) from exc
        fields = validate_direct_target_effect(
            effect,
            context,
            operation=self.operation,
            reference_field="card",
            family_label="Public-graveyard exile",
            allow_replacement_selections=True,
            additional_allowed_fields=(LINKED_CLUE_COPY_VERSION, "ability_pair") if linked_source is not None else (),
        )
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(
                ExilePublicGraveyardCardIntent(
                    actor=context.actor,
                    object_ref=fields.object_ref,
                    reason=fields.reason,
                    replacement_selections=fields.replacement_selections,
                    linked_source=linked_source,
                    linked_pair=linked_pair,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class MovePublicZoneSetHandler:
    handler_id: str = "generic.move-public-zone-set.v1"
    schema_version: int = 1
    family: str = "effect.public-zone-move"
    operation: str = "move_public_zone_set"
    rule_references: tuple[str, ...] = (
        "400.2",
        "400.7",
        "406.1",
        "406.2",
        "608.2c",
        "701.13a",
    )
    capability_dependencies: tuple[str, ...] = (
        "zone.move.fixed_public_set",
    )

    def lower(
        self,
        effect: Mapping[str, Any],
        context: ReadOnlyHandlerContext,
    ) -> IntentPlan:
        allowed = {
            "op",
            "source",
            "set",
            "followup",
            "reason",
            "_replacement_selections",
            "_aura_target_refs",
        }
        if set(effect) - allowed or not {"op", "source", "set"}.issubset(effect):
            raise SemanticNodeError(
                "Public zone-move set effect has an invalid shape"
            )
        if effect.get("op") != self.operation:
            raise SemanticNodeError(
                "Public zone-move set operation is unsupported"
            )
        from ..public_zone_moves import FixedMixedZoneExileSpec, is_mixed_zone_exile_spec

        raw = effect["set"]
        try:
            spec = (FixedMixedZoneExileSpec.from_dict(raw) if is_mixed_zone_exile_spec(raw)
                    else PublicZoneMoveSetSpec.from_dict(raw))
        except (PublicZoneMoveError, KeyError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc
        if isinstance(spec, PublicZoneMoveSetSpec) and spec.seat_relation is PublicZoneSeatRelation.TARGET_PLAYER:
            context.query.require_active_seat(str(spec.target_seat or ""))
        source_ref = effect.get("source")
        if source_ref is not None and (
            type(source_ref) is not str or not source_ref
        ):
            raise SemanticNodeError(
                "Public zone-move source must be a nonempty reference"
            )
        if isinstance(spec, PublicZoneMoveSetSpec) and spec.exclude_source and source_ref is None:
            raise SemanticNodeError(
                "Source-excluding public zone moves require a source"
            )
        reason = effect.get("reason") or context.default_reason
        if type(reason) is not str or not reason:
            raise SemanticNodeError(
                "Public zone-move reason must be a nonempty string"
            )
        selections = effect.get("_replacement_selections") or ()
        if not isinstance(selections, (list, tuple)):
            raise SemanticNodeError(
                "Public zone-move replacement selections must be an array"
            )
        if "_aura_target_refs" in effect and not isinstance(effect["_aura_target_refs"], Mapping):
            raise SemanticNodeError("Public Aura entry selections must be an object")
        try:
            intent = MovePublicZoneSetIntent(
                actor=context.actor,
                spec=spec,
                reason=reason,
                source_ref=source_ref,
                replacement_selections=tuple(selections),
                followup=PublicExileFollowup.from_dict(effect["followup"]) if "followup" in effect else None,
                aura_target_refs=effect.get("_aura_target_refs"),
            )
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(
            operation=self.operation,
            handler_id=self.handler_id,
            intents=(intent,),
        )


PUBLIC_ZONE_MOVE_HANDLERS = (
    ExilePublicGraveyardCardHandler(),
    MovePublicZoneSetHandler(),
    GraveyardCardCounterLifeHandler(),
    GraveyardExileBranchHandler(),
    GraveyardExileTokenHandler(),
    GraveyardExileTypeOutcomeHandler(),
)


__all__ = [
    "ExilePublicGraveyardCardHandler",
    "MovePublicZoneSetHandler",
    "PUBLIC_ZONE_MOVE_HANDLERS",
]
