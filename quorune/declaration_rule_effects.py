from __future__ import annotations

"""Typed resolution-created declaration rules stored in the effect journal."""

from dataclasses import dataclass
from typing import Any, Mapping, TypeAlias

from .continuous_effect_model import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectError,
    ContinuousObjectIdentity,
    decode_turn_expiry,
    validate_turn_expiry,
)
from .declaration_fragments import DeclarationRequirementTemplate, DeclarationRestrictionTemplate
from .defender_permission import DefenderAttackPermission
from .replacement.immutable import immutable_fingerprint
from .object_predicate import ObjectQuerySpec
from .source_continuity import SourceContinuitySnapshot
from .as_unblocked_rule import AS_UNBLOCKED_RULE_KIND,AsUnblockedAssignmentRule
from .toughness_assignment_rule import TOUGHNESS_RULE_KIND,ResolvedToughnessAssignmentRule
from .block_capacity_rule_model import BLOCK_CAPACITY_RULE_KIND, ResolvedBlockCapacityRule
from .temporary_cast_rule_model import (
    EFFECT_KIND as CAST_RESTRICTION_KIND, NEXT_TURN_SORCERY_TIMING_KIND,
    ResolvedCastRestriction, ResolvedNextTurnSorceryTiming,
)
from .draw_redirect_model import DRAW_REDIRECT_KIND, ResolvedDrawRedirect
from .target_combat_damage_multiplier_model import MULTIPLIER_KIND, ResolvedTargetCombatDamageMultiplier
from .next_draw_replacement_model import NEXT_DRAW_REPLACEMENT_KIND, ResolvedNextDrawReplacement
from .next_spell_cost_model import EFFECT_KIND as NEXT_SPELL_COST_KIND,ResolvedNextSpellCostReduction
from .source_block_pair_model import SOURCE_BLOCK_PAIR_EFFECT_KIND, ResolvedSourceBlockPair
from .library_search_model import LIBRARY_SEARCH_TEMP_KIND,ResolvedLibrarySearchProhibition
from .damage_death_exile_model import DAMAGE_DEATH_EXILE_KIND, ResolvedDamageDeathExile
from .linked_departure_exile_model import LINKED_DEPARTURE_EXILE_KIND, ResolvedLinkedDepartureExile
from .source_untap_rule_model import SOURCE_UNTAP_RESTRAINT_KIND, ResolvedSourceUntapRestraint
from .sacrifice_prohibition_model import SACRIFICE_PROHIBITION_KIND, ResolvedSacrificeProhibition
from .life_gain_permission import TEMPORARY_LIFE_GAIN_PROHIBITION_KIND, ResolvedLifeGainProhibition
from .damage_prevention_permission import TEMPORARY_DAMAGE_PROHIBITION_KIND, ResolvedDamagePreventionProhibition
from .game_hand_size_model import EFFECT_KIND as GAME_HAND_SIZE_KIND, ResolvedNoMaximumHandSize
from .turn_match_outcome_model import TURN_OUTCOME_FIELD, ResolvedTurnMatchOutcomeProtection
from .all_player_turn_rule_model import (
    ALL_PLAYER_TURN_RULE_KIND, TURN_LAND_MANA_TRIGGER_KIND, ResolvedAllPlayerTurnRule, ResolvedTurnLandManaTrigger,
)
from .protected_attack_payment_model import EFFECT_KIND as ATTACK_PAYMENT_KIND, ResolvedProtectedAttackPayment
from .chosen_attack_turn_model import KIND as CHOSEN_ATTACK_TURN_KIND, ResolvedChosenAttackTurn
from .next_turn_attack_restriction_model import ATTACK_RESTRICTION_EFFECT_KIND, ResolvedNextTurnAttackRestriction
from .counter_bound_attack_model import COUNTER_BOUND_ATTACK_KIND, ResolvedCounterBoundAttackRestriction


RESOLUTION_DECLARATION_RULE_EFFECT_KIND = "resolution_declaration_rule"
RESOLVED_ACTIVATION_PROHIBITION_KIND = "resolved_activation_prohibition"
OPPONENT_BLOCKING_PROHIBITION_KIND = "unblockable_by_opponents"
OPPONENT_BLOCKING_PROHIBITION = DeclarationRestrictionTemplate(
    template_id="temporary-blocking-opponents-prohibition-v1",
    declarations=("block",),
    scope="source_option",
)
ATTACK_BLOCK_PROHIBITION = DeclarationRestrictionTemplate(
    template_id="intrinsic-attack-block-prohibition-v1",
    declarations=("attack", "block"),
    scope="self",
)


def live_opponent_attack_query(controller: str) -> ObjectQuerySpec:
    return ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), excluded_controllers=(controller,))


def _live_attack_requirement_is_closed(requirement, query, expiry_player):
    if not isinstance(query, ObjectQuerySpec) or requirement.kind != "attack_each_combat":
        return False
    if expiry_player is not None:
        return query == live_opponent_attack_query(expiry_player)
    from .temporary_attack_requirement_model import TemporaryAttackRequirementSpec

    excluded = query.excluded_controllers
    return (len(excluded) == 1 and query == live_opponent_attack_query(excluded[0])
            and requirement == TemporaryAttackRequirementSpec().to_requirement())


def declaration_group_query_is_closed(query: ObjectQuerySpec, *, resolved: bool = False) -> bool:
    """Keep live game-rule recipients on current public creature predicates."""
    return bool(
        isinstance(query, ObjectQuerySpec)
        and query.zones == ("battlefield",)
        and "creature" in query.types_all
        and query.owner is None
        and (resolved or query.controller is None and not query.excluded_controllers)
        and (query.controller is None or not query.controller.startswith("$"))
        and all(not controller.startswith("$") for controller in query.excluded_controllers)
        and not query.include_phased_out
        and query.known_to_actor is None
        and query.exclude_ref is None
    )


SOURCE_CONTROLLED_BLOCK_FIELD = "controlled_source_block_version"


@dataclass(frozen=True, slots=True)
class ControlledDeclarationSource:
    identity: ContinuousObjectIdentity
    controller: str
    history: SourceContinuitySnapshot

    def __post_init__(self) -> None:
        if (not isinstance(self.identity, ContinuousObjectIdentity)
                or type(self.controller) is not str or not self.controller
                or self.controller.startswith("$") or not isinstance(self.history, SourceContinuitySnapshot)):
            raise ContinuousEffectError("A declaration duration requires its exact source and control history")

    def to_dict(self) -> dict[str, Any]:
        return {"identity": self.identity.to_dict(), "controller": self.controller, "history": self.history.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"identity", "controller", "history"}:
            raise ContinuousEffectError("Controlled declaration source has a closed shape")
        return cls(ContinuousObjectIdentity.from_dict(value["identity"]), value["controller"],
                   SourceContinuitySnapshot.from_dict(value["history"]))


def _validate_controlled_declaration_duration(effect, duration):
    if duration is not ContinuousEffectDuration.UNTIL_SOURCE_CONTROL_CHANGES:
        if effect.controlled_source is not None:
            raise ContinuousEffectError("Only a source-controlled declaration rule may carry a control interval")
        return
    if (not isinstance(effect.controlled_source, ControlledDeclarationSource)
            or effect.source_id != effect.controlled_source.identity.object_id or effect.live_query is not None
            or not isinstance(effect.restriction, DeclarationRestrictionTemplate)
            or effect.restriction.to_dict() != DeclarationRestrictionTemplate(
                template_id="intrinsic-block-prohibition-v1", declarations=("block",), scope="self",
            ).to_dict()
            or len(effect.locked_objects) != 1):
        raise ContinuousEffectError("A source-controlled duration requires its single locked blocking prohibition")


@dataclass(frozen=True, slots=True)
class ResolutionDeclarationRuleEffect:
    """One duration-bound declaration rule created by resolution.

    The affected object supplies the source-relative anchor used by the
    declaration solver, but the rule is not an ability or characteristic of
    that object.  Physical and logical identity keep the rule on the exact
    battlefield incarnation selected when it resolved. A live group query
    instead tests current public membership, including later entrants.
    """

    effect_id: str
    source_id: str
    timestamp: int
    restriction: DeclarationRestrictionTemplate | DeclarationRequirementTemplate | DefenderAttackPermission
    locked_objects: tuple[ContinuousObjectIdentity, ...]
    duration: ContinuousEffectDuration = (
        ContinuousEffectDuration.UNTIL_END_OF_TURN
    )
    live_query: ObjectQuerySpec | None = None
    expires_on_next_turn_of: str | None = None
    forbid_activations: bool = False
    block_opponents_of: str | None = None
    controlled_source: ControlledDeclarationSource | None = None

    def __post_init__(self) -> None:
        for name, value in (
            ("effect_id", self.effect_id),
            ("source_id", self.source_id),
        ):
            if type(value) is not str or not value:
                raise ContinuousEffectError(
                    f"Declaration-rule effect {name} must be a nonempty string"
                )
        if type(self.timestamp) is not int or self.timestamp < 0:
            raise ContinuousEffectError(
                "Declaration-rule effect timestamps must be nonnegative integers"
            )
        requirement = isinstance(self.restriction, DeclarationRequirementTemplate)
        live_requirement = requirement and self.live_query is not None
        defender_permission = isinstance(self.restriction, DefenderAttackPermission)
        if requirement and (
            (self.restriction.schema_version, self.restriction.kind) not in {
                (1, "all_able_blockers"), (1, "attack_each_combat"), (3, "attack_specific_player"),
            }
        ):
            raise ContinuousEffectError('Resolved requirements need one locked attack or all-able-blocker subject')
        if not (requirement or defender_permission or isinstance(self.restriction, DeclarationRestrictionTemplate)):
            raise ContinuousEffectError(
                "Declaration-rule effects require a typed restriction, requirement or permission"
            )
        if not requirement and not defender_permission and (
            self.restriction.mode != "prohibit"
            or self.restriction.scope not in {"self", "source_option"}
        ):
            raise ContinuousEffectError(
                "Resolution-created declaration rules require an "
                "object-anchored prohibition"
            )
        try:
            duration = ContinuousEffectDuration(self.duration)
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectError(
                "Declaration-rule effect duration is invalid"
            ) from exc
        if duration not in {
            ContinuousEffectDuration.UNTIL_END_OF_TURN, ContinuousEffectDuration.UNTIL_NEXT_TURN,
            ContinuousEffectDuration.UNTIL_SOURCE_CONTROL_CHANGES,
        }:
            raise ContinuousEffectError(
                "Resolution-created declaration rule duration is unsupported"
            )
        validate_turn_expiry(duration, self.expires_on_next_turn_of)
        _validate_controlled_declaration_duration(self, duration)
        if type(self.forbid_activations) is not bool or self.forbid_activations and (
            self.restriction != ATTACK_BLOCK_PROHIBITION
            or self.live_query is not None
            or duration is not ContinuousEffectDuration.UNTIL_NEXT_TURN
        ):
            raise ContinuousEffectError("Detain requires a locked next-turn attack and block prohibition")
        if live_requirement:
            if not _live_attack_requirement_is_closed(self.restriction, self.live_query, self.expires_on_next_turn_of):
                raise ContinuousEffectError("Live attack requirements need an exact opponent-creature query")
        elif requirement and self.expires_on_next_turn_of is not None:
            raise ContinuousEffectError("Resolved declaration requirements expire at end of turn")
        if defender_permission and duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN:
            raise ContinuousEffectError("Resolved Defender permissions expire at end of turn")
        object.__setattr__(self, "duration", duration)
        try:
            locked = tuple(sorted(tuple(self.locked_objects)))
        except (TypeError, AttributeError) as exc:
            raise ContinuousEffectError(
                "Declaration-rule locked identities must be unique typed values"
            ) from exc
        if (
            (not locked and self.live_query is None)
            or len(set(locked)) != len(locked)
            or not all(
                isinstance(identity, ContinuousObjectIdentity)
                for identity in locked
            )
        ):
            raise ContinuousEffectError(
                "Declaration-rule locked identities must be unique typed values"
            )
        object.__setattr__(self, "locked_objects", locked)
        if self.restriction == OPPONENT_BLOCKING_PROHIBITION or self.block_opponents_of is not None:
            if (self.restriction != OPPONENT_BLOCKING_PROHIBITION
                    or type(self.block_opponents_of) is not str or not self.block_opponents_of
                    or len(locked) != 1 or self.live_query is not None
                    or duration is not ContinuousEffectDuration.UNTIL_END_OF_TURN):
                raise ContinuousEffectError("Opponent blocking rules require one locked object and an original controller")
        if requirement and not live_requirement and len(locked) != 1:
            raise ContinuousEffectError('Resolved requirements lock exactly one battlefield incarnation')
        if defender_permission and self.live_query is None and len(locked) != 1:
            raise ContinuousEffectError("A locked Defender permission requires one battlefield incarnation")
        if self.live_query is not None and (
            locked or not declaration_group_query_is_closed(self.live_query, resolved=True)
        ):
            raise ContinuousEffectError("Live declaration rules require one public creature query and no locked objects")

    def to_dict(self) -> dict[str, Any]:
        value = {
            "effect_kind": RESOLUTION_DECLARATION_RULE_EFFECT_KIND,
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "duration": self.duration.value,
            "locked_objects": [
                identity.to_dict() for identity in self.locked_objects
            ],
        }
        if isinstance(self.restriction, DefenderAttackPermission):
            value.update(schema_version=5, permission=self.restriction.to_dict())
            if self.live_query is not None:
                value["live_query"] = self.live_query.to_dict()
        elif isinstance(self.restriction, DeclarationRequirementTemplate):
            value.update(schema_version=3, requirement=self.restriction.to_dict())
            if self.live_query is not None:
                version = 7 if self.expires_on_next_turn_of is not None else 8
                value.update(schema_version=version, live_query=self.live_query.to_dict())
        else:
            value['restriction'] = self.restriction.to_dict()
            if self.live_query is not None:
                value.update(schema_version=2, live_query=self.live_query.to_dict())
        if self.expires_on_next_turn_of is not None:
            value["expires_on_next_turn_of"] = self.expires_on_next_turn_of
        if self.forbid_activations:
            value.update(schema_version=4, forbid_activations=True)
        if self.block_opponents_of is not None:
            value.update(schema_version=6, block_opponents_of=self.block_opponents_of)
        if self.controlled_source is not None:
            value.update(schema_version=8, controlled_source=self.controlled_source.to_dict())
        return value

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, Any],
    ) -> "ResolutionDeclarationRuleEffect":
        expected = {
            "effect_kind",
            "effect_id",
            "source_id",
            "timestamp",
            "restriction",
            "duration",
            "locked_objects",
        }
        if not isinstance(value, Mapping):
            raise ContinuousEffectError("Declaration-rule effect fields are missing or unknown")
        turn_expiry = decode_turn_expiry(value)
        fields = set(value) - {"expires_on_next_turn_of"}
        live = fields == expected | {"schema_version", "live_query"}
        requirement_fields = (expected - {"restriction"}) | {"schema_version", "requirement"}
        live_requirement = fields == requirement_fields | {"live_query"}
        requirement = fields == requirement_fields or live_requirement
        detain = fields == expected | {"schema_version", "forbid_activations"}
        permission_fields = (expected - {"restriction"}) | {"schema_version", "permission"}
        permission = fields in (permission_fields, permission_fields | {"live_query"})
        opponent_blocking = fields == expected | {"schema_version", "block_opponents_of"}
        controlled = fields == expected | {"schema_version", "controlled_source"}
        if controlled and (type(value["schema_version"]) is not int or value["schema_version"] != 8):
            raise ContinuousEffectError("Controlled declaration rule schema version is malformed")
        if fields != expected and not (live or requirement or detain or permission or opponent_blocking or controlled):
            raise ContinuousEffectError(
                "Declaration-rule effect fields are missing or unknown"
            )
        if value["effect_kind"] != RESOLUTION_DECLARATION_RULE_EFFECT_KIND:
            raise ContinuousEffectError(
                "Declaration-rule effect kind is unsupported"
            )
        if live and (type(value["schema_version"]) is not int or value["schema_version"] != 2 or not isinstance(value["live_query"], Mapping)):
            raise ContinuousEffectError("Live declaration-rule schema version and query are malformed")
        live_requirement_version = 7 if turn_expiry is not None else 8
        if requirement and (type(value["schema_version"]) is not int
                            or value["schema_version"] != (live_requirement_version if live_requirement else 3)
                            or live_requirement and not isinstance(value["live_query"], Mapping)):
            raise ContinuousEffectError("Resolved requirement schema version is malformed")
        if detain and (type(value['schema_version']) is not int or value['schema_version'] != 4
                       or value['forbid_activations'] is not True):
            raise ContinuousEffectError('Detain declaration-rule schema version and scope are malformed')
        if permission and (type(value["schema_version"]) is not int or value["schema_version"] != 5
                           or ("live_query" in value and not isinstance(value["live_query"], Mapping))):
            raise ContinuousEffectError("Resolved Defender permission schema version or query is malformed")
        if opponent_blocking and (
                type(value["schema_version"]) is not int or value["schema_version"] != 6
                or type(value["block_opponents_of"]) is not str or not value["block_opponents_of"]):
            raise ContinuousEffectError("Opponent blocking rule schema version or controller is malformed")
        payload = value["permission"] if permission else value["requirement"] if requirement else value["restriction"]
        if not isinstance(payload, Mapping) or not isinstance(
            value["locked_objects"], list
        ):
            raise ContinuousEffectError(
                "Declaration-rule restriction and locked objects are malformed"
            )
        try:
            rule_type = (
                DefenderAttackPermission if permission
                else DeclarationRequirementTemplate if requirement
                else DeclarationRestrictionTemplate
            )
            return cls(
                effect_id=value["effect_id"],
                source_id=value["source_id"],
                timestamp=value["timestamp"],
                restriction=rule_type.from_dict(payload),
                duration=ContinuousEffectDuration(value["duration"]),
                locked_objects=tuple(
                    ContinuousObjectIdentity.from_dict(identity)
                    for identity in value["locked_objects"]
                ),
                live_query=ObjectQuerySpec.from_dict(value["live_query"]) if "live_query" in fields else None,
                expires_on_next_turn_of=turn_expiry,
                forbid_activations=detain,
                block_opponents_of=value.get("block_opponents_of") if opponent_blocking else None,
                controlled_source=ControlledDeclarationSource.from_dict(value["controlled_source"]) if controlled else None,
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ContinuousEffectError):
                raise
            raise ContinuousEffectError(str(exc)) from exc

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class ResolvedActivationProhibition:
    """One original permanent cannot activate abilities through this turn's cleanup."""

    effect_id: str
    source_id: str
    timestamp: int
    subject: ContinuousObjectIdentity

    def __post_init__(self) -> None:
        if (type(self.effect_id) is not str or not self.effect_id
                or type(self.source_id) is not str or not self.source_id
                or type(self.timestamp) is not int or self.timestamp < 0
                or not isinstance(self.subject, ContinuousObjectIdentity)):
            raise ContinuousEffectError("Activation prohibition requires a resolving source and one original subject")

    @property
    def duration(self) -> ContinuousEffectDuration:
        return ContinuousEffectDuration.UNTIL_END_OF_TURN

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "effect_kind": RESOLVED_ACTIVATION_PROHIBITION_KIND,
            "effect_id": self.effect_id,
            "source_id": self.source_id,
            "timestamp": self.timestamp,
            "subject": self.subject.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ResolvedActivationProhibition:
        fields = {"schema_version", "effect_kind", "effect_id", "source_id", "timestamp", "subject"}
        if (not isinstance(value, Mapping) or set(value) != fields
                or value["effect_kind"] != RESOLVED_ACTIVATION_PROHIBITION_KIND
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ContinuousEffectError("Activation prohibition journal fields are closed")
        return cls(
            value["effect_id"], value["source_id"], value["timestamp"],
            ContinuousObjectIdentity.from_dict(value["subject"]),
        )

    @property
    def fingerprint(self) -> str:
        return immutable_fingerprint(self.to_dict())


ContinuousJournalEffect: TypeAlias = (
    ContinuousEffect | ResolutionDeclarationRuleEffect | AsUnblockedAssignmentRule | ResolvedToughnessAssignmentRule | ResolvedBlockCapacityRule | ResolvedCastRestriction | ResolvedSourceBlockPair | ResolvedLibrarySearchProhibition | ResolvedDamageDeathExile | ResolvedSourceUntapRestraint
    | ResolvedLinkedDepartureExile | ResolvedNextSpellCostReduction | ResolvedLifeGainProhibition | ResolvedDamagePreventionProhibition
    | ResolvedProtectedAttackPayment | ResolvedDrawRedirect | ResolvedActivationProhibition | ResolvedNextDrawReplacement
    | ResolvedTurnMatchOutcomeProtection | ResolvedAllPlayerTurnRule | ResolvedTurnLandManaTrigger | ResolvedSacrificeProhibition | ResolvedNoMaximumHandSize
    | ResolvedNextTurnSorceryTiming | ResolvedNextTurnAttackRestriction | ResolvedTargetCombatDamageMultiplier
    | ResolvedCounterBoundAttackRestriction
    | ResolvedChosenAttackTurn
)


def continuous_journal_effect_from_dict(
    value: Mapping[str, Any],
) -> ContinuousJournalEffect:
    """Deserialize additive journal records while preserving legacy effects."""

    if isinstance(value, Mapping) and "effect_kind" in value:
        if value["effect_kind"] == RESOLVED_ACTIVATION_PROHIBITION_KIND:
            return ResolvedActivationProhibition.from_dict(value)
        if value["effect_kind"] == MULTIPLIER_KIND:
            return ResolvedTargetCombatDamageMultiplier.from_dict(value)
        if value["effect_kind"] == DRAW_REDIRECT_KIND:
            return ResolvedDrawRedirect.from_dict(value)
        if value["effect_kind"] == NEXT_DRAW_REPLACEMENT_KIND:
            return ResolvedNextDrawReplacement.from_dict(value)
        if value["effect_kind"] == ATTACK_PAYMENT_KIND:
            return ResolvedProtectedAttackPayment.from_dict(value)
        if value["effect_kind"] == ATTACK_RESTRICTION_EFFECT_KIND:
            return ResolvedNextTurnAttackRestriction.from_dict(value)
        if value["effect_kind"] == COUNTER_BOUND_ATTACK_KIND:
            return ResolvedCounterBoundAttackRestriction.from_dict(value)
        if value["effect_kind"] == CHOSEN_ATTACK_TURN_KIND:
            return ResolvedChosenAttackTurn.from_dict(value)
        if value["effect_kind"] == TEMPORARY_DAMAGE_PROHIBITION_KIND:
            return ResolvedDamagePreventionProhibition.from_dict(value)
        if value["effect_kind"] == ALL_PLAYER_TURN_RULE_KIND:
            return ResolvedAllPlayerTurnRule.from_dict(value)
        if value["effect_kind"] == TURN_LAND_MANA_TRIGGER_KIND:
            return ResolvedTurnLandManaTrigger.from_dict(value)
        if value["effect_kind"] == GAME_HAND_SIZE_KIND:
            return ResolvedNoMaximumHandSize.from_dict(value)
        if value["effect_kind"] == TURN_OUTCOME_FIELD:
            return ResolvedTurnMatchOutcomeProtection.from_dict(value)
        if value["effect_kind"] == TEMPORARY_LIFE_GAIN_PROHIBITION_KIND:
            return ResolvedLifeGainProhibition.from_dict(value)
        if value['effect_kind']==NEXT_SPELL_COST_KIND:return ResolvedNextSpellCostReduction.from_dict(value)
        if value["effect_kind"] == SACRIFICE_PROHIBITION_KIND:
            return ResolvedSacrificeProhibition.from_dict(value)
        if value["effect_kind"] == SOURCE_UNTAP_RESTRAINT_KIND:
            return ResolvedSourceUntapRestraint.from_dict(value)
        if value['effect_kind'] == DAMAGE_DEATH_EXILE_KIND:
            return ResolvedDamageDeathExile.from_dict(value)
        if value['effect_kind'] == LINKED_DEPARTURE_EXILE_KIND:
            return ResolvedLinkedDepartureExile.from_dict(value)
        if value['effect_kind']==LIBRARY_SEARCH_TEMP_KIND:return ResolvedLibrarySearchProhibition.from_dict(value)
        if value['effect_kind'] == SOURCE_BLOCK_PAIR_EFFECT_KIND:return ResolvedSourceBlockPair.from_dict(value)
        if value['effect_kind']==CAST_RESTRICTION_KIND:return ResolvedCastRestriction.from_dict(value)
        if value["effect_kind"] == NEXT_TURN_SORCERY_TIMING_KIND:
            return ResolvedNextTurnSorceryTiming.from_dict(value)
        if value['effect_kind'] == BLOCK_CAPACITY_RULE_KIND:
            return ResolvedBlockCapacityRule.from_dict(value)
        if value['effect_kind']==AS_UNBLOCKED_RULE_KIND:return AsUnblockedAssignmentRule.from_dict(value)
        if value['effect_kind']==TOUGHNESS_RULE_KIND:return ResolvedToughnessAssignmentRule.from_dict(value)
        return ResolutionDeclarationRuleEffect.from_dict(value)
    return ContinuousEffect.from_dict(value)


__all__ = [
    "ATTACK_BLOCK_PROHIBITION",
    "ContinuousJournalEffect",
    "RESOLUTION_DECLARATION_RULE_EFFECT_KIND",
    "ResolutionDeclarationRuleEffect",
    "continuous_journal_effect_from_dict",
]
