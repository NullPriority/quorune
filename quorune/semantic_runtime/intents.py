from __future__ import annotations

from ..rules.departing_x_reflexive import PaidDepartureDamageIntent
from ..bounded_hand_cast_model import ResolveBoundedHandCastIntent
from ..stack_spell_copy_model import PermanentSpellCopySpec

from ..city_blessing_model import AscendIntent
from ..counter_placement_sets import validate_counter_set_recipient_authority
from ..cipher_model import EncodeCipherIntent
from .soulbond_intent import PairSoulbondIntent
from .earthbend_intents import EarthbendIntent, FinishEarthbendIntent, ReturnEarthbentObjectsIntent
from ..suspect_model import SuspectIntent
from ..preparation_model import PrepareSourceIntent
from ..combat_removal import CombatRemovalIntent
from ..whole_hand_discard_model import DiscardWholeHandsIntent
from ..discard_result_model import DiscardWholeHandsResultIntent
from ..hand_library_redraw_model import HandLibraryRedrawIntent
from ..drawing.captured_hand import RevealCapturedDrawsIntent, ResolveCapturedDrawCastIntent
from ..dungeon_progress import VentureProgressIntent
from ..initiative_model import TakeInitiativeIntent
from ..random_discard import RandomDiscardIntent
from ..rules.graveyard_card_targets import GraveyardReturnScope
from ..rules.random_graveyard_returns import RandomGraveyardReturnIntent
from .graveyard_recycling import GraveyardRecyclingIntent
from ..target_land_type_model import TargetLandTypeSettingIntent
from ..source_sacrifice_model import FixedSourceSacrificeIntent
from ..temporary_cast_rule_model import TemporaryCastRestrictionIntent
from ..draw_redirect_model import TemporaryDrawRedirectIntent
from ..target_combat_damage_multiplier_model import TargetCombatDamageMultiplierIntent
from ..next_draw_replacement_model import ScheduleNextDrawResultIntent
from ..game_hand_size_model import NoMaximumForGameIntent
from ..protected_attack_payment_model import TemporaryProtectedAttackPaymentIntent
from ..next_turn_attack_restriction_model import NextTurnAttackRestrictionIntent
from ..counter_bound_attack_model import CounterBoundAttackRestrictionIntent
from ..extra_combat_model import ScheduleExtraCombatIntent
from ..additional_beginning_model import ScheduleAdditionalBeginningIntent
from ..rules.token_mill_repeat import TokenMillRepeatIntent
from ..turn_skip_model import ScheduleTurnSkipIntent
from ..next_player_untap_model import ProhibitNextPlayerUntapIntent
from ..library_search_model import ProhibitLibrarySearchIntent
from .control_intents import ControlIntent
from .domain_intent import DomainEffectIntent
from .entry_enchantment_return_intent import EntryEnchantmentReturnIntent
from .zone_move_intent import (
    MillCardsIntent, ZoneMoveIntent, ExilePublicGraveyardCardIntent, MovePublicZoneSetIntent,
    MoveObjectsSimultaneouslyIntent, _freeze_replacement_selections,
)
from .counter_movement_intent import CounterMovementIntent
from .connive_intent import ConniveCompletedIntent
from .ring_intent import BeginRingTemptationIntent, CompleteRingBearerIntent
from .creation_intents import (
    CreateCompiledEmblemIntent, CreateRegenerationShieldIntent, CreateRegenerationShieldSetIntent,
)
from .champion_intent import ChampionExileIntent, ReturnLinkedAbilityExileIntent
from .energy_payment import PayEnergyIntent
from ..reflexive_trigger_model import QueueReflexiveTriggerIntent, QueueRevealUntilReflexiveIntent
from ..source_untap_rule_model import PendingSourceUntapRestraint

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TYPE_CHECKING, TypeAlias

from ..ordered_incarnation_exclusion import OrderedIncarnationExclusion
from ..affected_permanents import AffectedPermanentSetSpec
from ..rules.destruction_result_model import DestructionFollowup
from ..rules.artifact_destruction_treasure import ArtifactDestructionTreasure
from ..rules.destroyed_creature_copy import DestroyedCreatureCopy
from ..rules.nonbasic_land_source_untap import NonbasicLandSourceUntap
from ..rules.target_controller_library_reveal import TargetControllerLibraryReveal
from ..rules.announced_destruction_controller_tokens import AnnouncedDestructionControllerTokens
from ..rules.controlled_destruction_source_counters import ControlledDestructionSourceCounters
from ..rules.revealed_library_top import RevealedLibraryTopSpec
from ..public_zone_moves import PublicZoneMoveSetSpec
from ..drawing.model import DrawCardsIntent
from ..announced_damage import AllocatedDamageRecipient
from ..fixed_damage_set_model import FixedDamageSetSpec, dealt_life_gain_group_is_closed
from ..damage_death_exile_model import DamageDeathExileScope
from ..fractional_quantity_model import HalfQuantitySpec
from ..impulse_access_model import (
    ImpulseAccessDuration, impulse_access_request_count_is_legal, impulse_access_play_policy_is_legal,
)
from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap, thaw_value
from ..rules.library_scry import ScryArrangement
from .library_selection_intent import LibrarySelectionIntent
from ..rules.ripple_resolution_model import ResolveRippleCastIntent,RippleBottomIntent
from ..rules.library_surveillance import SurveilArrangement
from ..rules.hand_inspection import InspectHandIntent
from .colored_hand_reveal import RevealColoredHandCardsIntent
from ..zone_trigger_events import ZoneTransitionKind
from .composition_intent import CompositionIntent,ResumeCompositionOrderIntent
from .context import SemanticSourceContext

if TYPE_CHECKING:
    from ..stack_counter import CounterActivationProhibition
from .madness_intent import MadnessChoiceIntent
from .miracle_intent import MiracleCastChoiceIntent
from ..discover_model import DiscoverCastIntent,DiscoverCleanupIntent
from ..fixed_top_exile import ResolvingExileLandPlayIntent
from ..rules.shared_type_library_play_model import ResolvingSharedTypeLibraryPlayIntent
from ..rules.qualified_library_cast import ResolveQualifiedLibraryCastIntent
from ..targeted_graveyard_cast_model import GraveyardCastIntent
from ..rules.graveyard_copy_cast_model import ExileGraveyardCopiesIntent


_EXPLORE_LABEL = "Explore"
_REASON_FIELD = "reason"


@dataclass(frozen=True, slots=True)
class ImpulseAccessIntent:
    actor: str
    player: str
    count: int | HalfQuantitySpec
    duration: ImpulseAccessDuration | None
    reason: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Impulse-access intents require an actor, player, and reason"
            )
        if not impulse_access_request_count_is_legal(self.count, self.schema_version):
            raise ValueError("Impulse-access intent count is invalid for its version")
        if not impulse_access_play_policy_is_legal(self.actor, self.player, self.duration, self.schema_version):
            raise ValueError("Impulse-access intents require their typed supplier and play policy")


@dataclass(frozen=True, slots=True)
class BecomeMonarchIntent:
    player: str
    reason: str


@dataclass(frozen=True, slots=True)
class SetPermanentTappedIntent:
    object_ref: str
    actor: str
    tapped: bool
    reason: str
    logical_object_id: str | None = None
    untap_restraint: PendingSourceUntapRestraint | None = None


@dataclass(frozen=True, slots=True)
class ProhibitNextUntapIntent:
    object_ref: str
    actor: str
    reason: str
    logical_object_id: str | None = None
    step_count: int = 1


@dataclass(frozen=True, slots=True)
class SetPermanentsTappedIntent:
    object_refs: tuple[str, ...]
    actor: str
    tapped: bool
    reason: str
    prohibit_next_untap: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.object_refs, (list, tuple))
            or any(
                type(value) is not str or not value
                for value in self.object_refs
            )
            or len(self.object_refs) != len(set(self.object_refs))
            or type(self.actor) is not str
            or not self.actor
            or type(self.tapped) is not bool
            or type(self.prohibit_next_untap) is not bool
            or (self.prohibit_next_untap and not self.tapped)
            or type(self.reason) is not str
            or not self.reason
        ):
            raise ValueError("Target-set tap-state intent is malformed")
        object.__setattr__(self, "object_refs", tuple(self.object_refs))


@dataclass(frozen=True, slots=True)
class UntapAllCreaturesIntent:
    actor: str
    reason: str




@dataclass(frozen=True, slots=True)
class DestroyPermanentIntent:
    actor: str
    object_ref: str
    reason: str
    regeneration_prohibited: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    followup: ArtifactDestructionTreasure | NonbasicLandSourceUntap | DestroyedCreatureCopy | TargetControllerLibraryReveal | None = None

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Destruction intents require actor, object, and reason"
            )
        if type(self.regeneration_prohibited) is not bool:
            raise ValueError(
                "Destruction regeneration prohibition must be boolean"
            )
        if self.followup is not None and (
            not isinstance(self.followup, (ArtifactDestructionTreasure, NonbasicLandSourceUntap, DestroyedCreatureCopy, TargetControllerLibraryReveal))
            or self.regeneration_prohibited
        ):
            raise ValueError("Destruction results require their typed ordinary destruction follow-up")
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Destruction",
            ),
        )


@dataclass(frozen=True, slots=True)
class DestroyPermanentSetIntent:
    actor: str
    spec: AffectedPermanentSetSpec
    reason: str
    source_ref: str | None = None
    regeneration_prohibited: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    followup: DestructionFollowup | None = None
    ordered_exclusion: OrderedIncarnationExclusion | None = None

    def __post_init__(self) -> None:
        if self.ordered_exclusion is not None and (
            not isinstance(self.ordered_exclusion, OrderedIncarnationExclusion)
            or self.followup is not None and (
                not isinstance(self.followup, DestructionFollowup) or not self.followup.uses_owned_return
                or self.ordered_exclusion.protected
            )
        ):
            raise ValueError("Ordered destruction requires its typed incarnation exclusion")
        if self.followup is not None and not isinstance(self.followup, DestructionFollowup):
            raise ValueError("Destruction-set follow-up must be typed")
        if self.followup is not None and self.followup.uses_owned_return and self.ordered_exclusion is None:
            raise ValueError("An owned destruction return requires its ordered batch")
        if any(type(value) is not str or not value for value in (self.actor, self.reason)):
            raise ValueError(
                "Destruction-set intents require actor and reason"
            )
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError(
                "Destruction-set intents require a typed affected set"
            )
        if type(self.regeneration_prohibited) is not bool:
            raise ValueError(
                "Destruction-set regeneration prohibition must be boolean"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Destruction-set source must be a nonempty reference"
            )
        if self.spec.exclude_source and self.source_ref is None:
            raise ValueError(
                "Source-excluding destruction sets require a source"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Destruction-set",
            ),
        )


@dataclass(frozen=True, slots=True)
class DestroyPermanentTargetsIntent:
    actor: str
    object_refs: tuple[str, ...]
    reason: str
    regeneration_prohibited: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    followup: AnnouncedDestructionControllerTokens | ControlledDestructionSourceCounters | None = None

    def __post_init__(self) -> None:
        if (
            type(self.actor) is not str
            or not self.actor
            or not isinstance(self.object_refs, (list, tuple))
            or any(
                type(value) is not str or not value
                for value in self.object_refs
            )
            or len(self.object_refs) != len(set(self.object_refs))
            or type(self.reason) is not str
            or not self.reason
            or type(self.regeneration_prohibited) is not bool
        ):
            raise ValueError("Target-set destruction intent is malformed")
        if self.followup is not None and (
            not isinstance(self.followup, (AnnouncedDestructionControllerTokens, ControlledDestructionSourceCounters))
            or self.regeneration_prohibited or isinstance(self.followup, AnnouncedDestructionControllerTokens)
            and self.followup.requires_graveyard and len(self.object_refs) > 3
        ):
            raise ValueError("Destruction rewards require a typed ordinary target-set result")
        object.__setattr__(self, "object_refs", tuple(self.object_refs))
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Target-set destruction",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReturnPermanentToOwnerHandIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Return intents require actor, object, and reason"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="return-to-owner-hand",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReturnGraveyardCardToOwnerHandIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    target_snapshot: FrozenMap | None = None
    scope: GraveyardReturnScope = GraveyardReturnScope.OWN

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Graveyard-return intents require actor, object, and reason"
            )
        if not isinstance(self.scope, GraveyardReturnScope):
            raise ValueError("Graveyard-return intent scope is unsupported")
        if self.scope is GraveyardReturnScope.PUBLIC and self.target_snapshot is None:
            raise ValueError("Public graveyard return requires its announced target incarnation")
        if self.target_snapshot is not None:
            snapshot=self.target_snapshot
            if (not isinstance(snapshot,Mapping) or set(snapshot)!={'ref','object_id','zone_change_counter'}
                    or snapshot['ref']!=self.object_ref or type(snapshot['object_id']) is not str or not snapshot['object_id']
                    or type(snapshot['zone_change_counter']) is not int or snapshot['zone_change_counter']<0):
                raise ValueError('Graveyard-return target identity snapshot is malformed')
            object.__setattr__(self,'target_snapshot',FrozenMap(snapshot))
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="graveyard-card-return-to-owner-hand",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExilePermanentIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Permanent-exile intents require actor, object, and reason"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Permanent-exile",
            ),
        )






@dataclass(frozen=True, slots=True)
class DealFixedDamageSetIntent:
    actor: str
    source_ref: str
    amount: int
    spec: FixedDamageSetSpec | None
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    replacement_event_ids: tuple[str, ...] = ()
    source_snapshot: FrozenMap | None = None
    death_exile_scope: DamageDeathExileScope | None = None
    allocated_recipients: tuple[AllocatedDamageRecipient, ...] | None = None
    gain_life_from_dealt: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.source_ref, self.reason)
        ):
            raise ValueError(
                "Fixed damage-set intents require actor, source, and reason"
            )
        if type(self.amount) is not int or self.amount < 0:
            raise ValueError(
                "Fixed damage-set intent amount must be a nonnegative integer"
            )
        if self.allocated_recipients is None:
            if not isinstance(self.spec, FixedDamageSetSpec):
                raise ValueError("Fixed damage-set intents require a typed recipient set")
        else:
            recipients = self.allocated_recipients
            if (self.spec is not None or self.death_exile_scope is not None
                    or not isinstance(recipients, tuple)
                    or any(not isinstance(recipient, AllocatedDamageRecipient) for recipient in recipients)
                    or len({recipient.ref for recipient in recipients}) != len(recipients)
                    or sum(recipient.amount for recipient in recipients) > self.amount):
                raise ValueError("Announced damage intents require distinct recipients and bounded shares")
        if type(self.gain_life_from_dealt) is not bool or self.gain_life_from_dealt and (
            not dealt_life_gain_group_is_closed(self.spec) or self.allocated_recipients is not None
            or self.death_exile_scope is not None or self.amount <= 0
        ):
            raise ValueError("Dealt damage life gain requires one exact fixed player group")
        if self.death_exile_scope is not None and self.death_exile_scope is not DamageDeathExileScope.DEALT_CREATURES:
            raise ValueError('Fixed damage-set death exile follows actual damaged creatures')
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Fixed damage-set",
            ),
        )
        event_ids = tuple(self.replacement_event_ids)
        if any(type(value) is not str or not value for value in event_ids):
            raise ValueError(
                "Fixed damage-set replacement event identities are invalid"
            )
        if len(event_ids) != len(set(event_ids)):
            raise ValueError(
                "Fixed damage-set replacement event identities must be unique"
            )
        object.__setattr__(self, "replacement_event_ids", event_ids)
        if self.source_snapshot is not None:
            from ..damage_source import DamageSourceSnapshot
            snapshot = DamageSourceSnapshot.from_dict(dict(thaw_value(self.source_snapshot)))
            if snapshot.ref != self.source_ref:
                raise ValueError('Fixed damage-set source snapshot identity changed')
            object.__setattr__(self, 'source_snapshot', FrozenMap(snapshot.to_dict()))


@dataclass(frozen=True, slots=True)
class AddManaIntent:
    player: str
    color: str
    amount: int
    actor: str
    reason: str
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SetCardDesignationIntent:
    object_ref: str
    designation: Literal[
        "chosen_name", "chosen_creature_type", "chosen_color", "chosen_faction",
        "chosen_jeskai_temur_faction", "last_noted_mana_type", "chosen_basic_land_type",
    ]
    value: str
    actor: str
    reason: str
    apply_as_subtype: bool = False

    def __post_init__(self) -> None:
        if self.designation not in {
            "chosen_name",
            "chosen_creature_type",
            "chosen_color",
            "chosen_faction",
            "chosen_jeskai_temur_faction",
            "chosen_basic_land_type",
            "last_noted_mana_type",
        }:
            raise ValueError("Card designation kind is unsupported")
        if any(
            type(value) is not str or not value
            for value in (
                self.object_ref,
                self.value,
                self.actor,
                self.reason,
            )
        ):
            raise ValueError(
                "Card designation identifiers and text must be nonempty strings"
            )
        if type(self.apply_as_subtype) is not bool:
            raise ValueError("Designation subtype application must be boolean")
        if self.apply_as_subtype and self.designation != "chosen_creature_type":
            raise ValueError(
                "Only a chosen creature type may become a subtype"
            )
        if self.designation == "chosen_color" and self.value not in tuple("WUBRG"):
            raise ValueError("A chosen color must be one WUBRG symbol")
        if self.designation == "last_noted_mana_type":
            from ..rules.noted_mana import NOTED_MANA_TYPES

            if self.value not in NOTED_MANA_TYPES:
                raise ValueError("Noted mana requires one ordinary mana type")
        if self.designation == "chosen_basic_land_type":
            from ..entry_designations import EntryDesignationKind, validate_designation

            validate_designation(EntryDesignationKind.BASIC_LAND_TYPE, self.value)
        if self.designation in {"chosen_faction", "chosen_jeskai_temur_faction"}:
            from ..entry_designations import EntryDesignationKind, validate_designation

            kind = (EntryDesignationKind.FACTION if self.designation == "chosen_faction"
                    else EntryDesignationKind.JESKAI_TEMUR_FACTION)
            validate_designation(kind, self.value)


@dataclass(frozen=True, slots=True)
class BecomeMonstrousIntent:
    actor: str
    object_id: str
    object_ref: str
    logical_object_id: str
    value: int
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.reason,
            )
        ):
            raise ValueError(
                "Monstrous intents require complete object identity"
            )
        if type(self.value) is not int or self.value < 0:
            raise ValueError(
                "Monstrous intents require an exact nonnegative value"
            )


@dataclass(frozen=True, slots=True)
class BecomeRenownedIntent:
    actor: str
    object_id: str
    object_ref: str
    logical_object_id: str
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.reason,
            )
        ):
            raise ValueError(
                "Renowned intents require complete object identity"
            )


@dataclass(frozen=True, slots=True)
class RecordChoiceIntent:
    actor: str
    event_code: str
    message: str
    details: FrozenMap
    importance: int = 1
    visibility: tuple[str, ...] | None = None
    changed_object_refs: tuple[str, ...] = ()
    changed_players: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.details, FrozenMap):
            object.__setattr__(self, "details", FrozenMap(self.details))


@dataclass(frozen=True, slots=True)
class ExploreCompletedIntent:
    actor: str
    player: str
    explorer_ref: str
    explorer_logical_object_id: str
    result: Literal[
        "empty_library",
        "land_revealed",
        "nonland_graveyard_choice",
        "nonland_top_choice",
    ]
    reason: str
    revealed_card_ref: str | None = None
    explorer_types: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("actor", self.actor),
            ("player", self.player),
            ("explorer_ref", self.explorer_ref),
            ("explorer_logical_object_id", self.explorer_logical_object_id),
            (_REASON_FIELD, self.reason),
        ):
            if type(value) is not str or not value:
                raise ValueError(
                    f"{_EXPLORE_LABEL} {label} must be a nonempty string"
                )
        if self.revealed_card_ref is not None and (
            type(self.revealed_card_ref) is not str
            or not self.revealed_card_ref
        ):
            raise ValueError(
                "Explore revealed-card reference must be nonempty or null"
            )
        if self.explorer_types is not None and (
            type(self.explorer_types) is not tuple
            or any(type(value) is not str or not value or value != value.casefold()
                   for value in self.explorer_types)
            or len(set(self.explorer_types)) != len(self.explorer_types)
        ):
            raise ValueError('Explore completion types require immutable canonical terms or unavailable')


@dataclass(frozen=True, slots=True)
class ChooseOneRestBottomRandomIntent:
    actor: str
    player: str
    chosen_ref: str
    looked_refs: tuple[str, ...]
    reason: str
    source_stack_ref: str
    event_code: str = "library.choose_one_rest_bottom_random"


@dataclass(frozen=True, slots=True)
class ShuffleLibraryIntent:
    actor: str
    player: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReturnCardsToLibraryTopIntent:
    actor: str
    player: str
    refs_top_first: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class RecordZoneMoveIntent:
    actor: str
    object_ref: str
    event_code: str
    message: str
    details: FrozenMap
    importance: int = 2
    changed_player: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.details, FrozenMap):
            object.__setattr__(self, "details", FrozenMap(self.details))


@dataclass(frozen=True, slots=True)
class LifeChangeIntent:
    actor: str
    player: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Life-change intents require actor, player, and reason"
            )
        if type(self.amount) is not int:
            raise ValueError("Life-change intent amounts must be integers")
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Life-change intent sources must be nonempty references"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Life change",
            ),
        )


@dataclass(frozen=True, slots=True)
class PayLifeIntent:
    actor: str
    player: str
    amount: int
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Life-payment intents require actor, player, and reason"
            )
        if type(self.amount) is not int or self.amount < 0:
            raise ValueError(
                "Life-payment intent amounts must be nonnegative integers"
            )


@dataclass(frozen=True, slots=True)
class RevealLibraryCardsIntent:
    actor: str
    player: str
    viewer: str
    refs_top_first: tuple[str, ...]
    reason: str
    public: bool = False
    announce_public_top: bool = False
    announce_public_prefix: bool = False
    retain_public_top_visibility: bool = False


@dataclass(frozen=True, slots=True)
class RevealEachLibraryTopIntent:
    actor: str
    reason: str
    followup: RevealedLibraryTopSpec


@dataclass(frozen=True, slots=True)
class MoveLibraryCardsToBottomIntent:
    actor: str
    player: str
    refs: tuple[str, ...]
    looked_count: int
    reason: str
    random_source_stack_ref: str | None = None

    def __post_init__(self) -> None:
        if self.random_source_stack_ref is not None and (
            type(self.random_source_stack_ref) is not str or not self.random_source_stack_ref
            or self.actor != self.player or not self.refs
            or type(self.looked_count) is not int or not len(self.refs) <= self.looked_count <= 5
            or any(type(ref) is not str or not ref for ref in self.refs)
            or len(self.refs) != len(set(self.refs))
        ):
            raise ValueError("Random library bottom placement needs its original source and remainder")


@dataclass(frozen=True, slots=True)
class ScryLibraryIntent:
    actor: str
    player: str
    arrangement: ScryArrangement
    reason: str
    requested_count: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.arrangement, ScryArrangement):
            raise TypeError("Scry intents require an immutable arrangement")
        if self.requested_count is not None and (
            type(self.requested_count) is not int or self.requested_count <= 0
        ):
            raise ValueError("Scry requested count must be a positive integer")


@dataclass(frozen=True, slots=True)
class SurveilLibraryIntent:
    actor: str
    player: str
    arrangement: SurveilArrangement
    requested_count: int
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Surveil intents require an actor, player, and reason"
            )
        if not isinstance(self.arrangement, SurveilArrangement):
            raise TypeError("Surveil intents require an immutable arrangement")
        if type(self.requested_count) is not int or self.requested_count <= 0:
            raise ValueError(
                "Surveil intents require a positive fixed count"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Surveil",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReorderLibraryTopIntent:
    actor: str
    player: str
    viewer: str
    refs_top_first: tuple[str, ...]
    reason: str
    shuffle_remainder: bool = False


@dataclass(frozen=True, slots=True)
class PayManaCostIntent:
    actor: str
    player: str
    requirements: FrozenMap
    reason: str
    event_code: str
    message: str
    details: FrozenMap
    changed_object_ref: str | None = None
    pool_payment: FrozenMap | None = None

    def __post_init__(self) -> None:
        if self.pool_payment is not None and not isinstance(self.pool_payment, FrozenMap):
            object.__setattr__(self, "pool_payment", FrozenMap(self.pool_payment))
        if not isinstance(self.requirements, FrozenMap):
            object.__setattr__(
                self,
                "requirements",
                FrozenMap(self.requirements),
            )
        if not isinstance(self.details, FrozenMap):
            object.__setattr__(self, "details", FrozenMap(self.details))


@dataclass(frozen=True, slots=True)
class PlaceCountersIntent:
    actor: str
    object_refs: tuple[str, ...]
    counter_name: str | None
    amount: int | Literal["existing_named_counter_count","existing_all_counter_counts"]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    effect_generated: bool = True
    ordered_cost_version: int | None = None

    def __post_init__(self) -> None:
        if self.ordered_cost_version is not None and (
            type(self.ordered_cost_version) is not int or self.ordered_cost_version != 2
            or self.effect_generated is not False or type(self.amount) is not int or self.amount != 1
        ):
            raise ValueError("Ordered counter cost occurrences require explicit version two")
        if type(self.effect_generated) is not bool:
            raise ValueError("Counter placement provenance must be boolean")
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class RemoveCountersIntent:
    actor: str
    object_ref: str
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    gain_life_from_removed: bool = False

    def __post_init__(self) -> None:
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.object_ref, normalized, self.reason)
        ):
            raise ValueError(
                "Counter-removal intents require actor, object, counter, and reason"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Counter-removal intent amount must be a positive exact integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Counter-removal intent source must be a nonempty reference"
            )
        if (type(self.gain_life_from_removed) is not bool or self.gain_life_from_removed
                and (self.source_ref != self.object_ref or normalized != "+1/+1")):
            raise ValueError("Life from counter removal requires the original source's +1/+1 counters")
        object.__setattr__(self, "counter_name", normalized)


@dataclass(frozen=True, slots=True)
class RemoveAllCountersIntent:
    actor: str
    object_ref: str
    reason: str
    source_ref: str | None = None
    counter_name: str | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.object_ref, self.reason)
        ):
            raise ValueError(
                "All-counter removal intents require actor, object, and reason"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "All-counter removal intent source must be a nonempty reference"
            )
        if self.counter_name is not None:
            from ..counter_names import normalized_counter_name
            if type(self.counter_name) is not str or normalized_counter_name(self.counter_name) != self.counter_name:
                raise ValueError('Named-kind removal intent requires a canonical counter name')


@dataclass(frozen=True, slots=True)
class CounterPlacementAmount:
    counter_name: str
    amount: int

    def __post_init__(self) -> None:
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if not normalized or type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Counter batch entries require a name and positive exact amount"
            )
        object.__setattr__(self, "counter_name", normalized)


@dataclass(frozen=True, slots=True)
class PlaceCounterBatchIntent:
    actor: str
    object_ref: str
    placements: tuple[CounterPlacementAmount, ...]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    paired_source_ref: str | None = None
    recipient_identity: str | None = None
    source_departure: bool = False

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.object_ref, self.reason)
        ):
            raise ValueError(
                "Counter batch intents require actor, object, and reason"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError("Counter batch source must be a nonempty reference")
        if self.paired_source_ref is not None and (
            type(self.paired_source_ref) is not str or not self.paired_source_ref
            or self.paired_source_ref == self.object_ref or self.paired_source_ref != self.source_ref
        ):
            raise ValueError("The paired source must be a distinct original battlefield reference")
        placements = tuple(self.placements)
        if type(self.source_departure) is not bool or (
            self.source_departure and self.paired_source_ref is not None
        ):
            raise ValueError("A source departure requires a typed, unpaired counter batch")
        if self.recipient_identity is not None and (
            type(self.recipient_identity) is not str
            or not self.recipient_identity
            or self.paired_source_ref is not None
        ):
            raise ValueError("A departure counter batch pins one original controlled recipient")
        if self.recipient_identity is not None or self.source_departure:
            allowed_count = bool(placements)
        else:
            allowed_count = 2 <= len(placements) <= 3
        if not allowed_count or any(not isinstance(value, CounterPlacementAmount) for value in placements):
            raise ValueError("Counter batch placements do not match their exact source shape")
        names = [value.counter_name for value in placements]
        if len(names) != len(set(names)):
            raise ValueError("Counter batch kinds must be distinct")
        object.__setattr__(self, "placements", placements)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter batch placement",
            ),
        )

    @property
    def object_refs(self) -> tuple[str, ...]:
        if self.paired_source_ref is None:
            return (self.object_ref,)
        return (self.object_ref, self.paired_source_ref)


@dataclass(frozen=True, slots=True)
class PlaceCountersOnSetIntent:
    actor: str
    spec: AffectedPermanentSetSpec
    counter_name: str
    amount: int | Literal["existing_named_counter_count"]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    paired_loyalty_source: tuple[str, str] | None = None
    source_and_subtype_identity: tuple[str, str] | None = None
    declared_attack_members: tuple[tuple[str, str], ...] | None = None

    def __post_init__(self) -> None:
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if any(
            type(value) is not str or not value
            for value in (self.actor, normalized, self.reason)
        ):
            raise ValueError(
                "Counter-set intents require actor, counter, and reason"
            )
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError(
                "Counter-set intents require a typed affected set"
            )
        if self.amount != "existing_named_counter_count" and (type(self.amount) is not int or self.amount <= 0):
            raise ValueError(
                "Counter-set intent amount must be a positive exact integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Counter-set intent source must be a nonempty reference"
            )
        if self.spec.exclude_source and self.source_ref is None:
            raise ValueError(
                "Source-excluding counter sets require a source"
            )
        members = validate_counter_set_recipient_authority(self, normalized)
        object.__setattr__(self, "declared_attack_members", members)
        object.__setattr__(self, "counter_name", normalized)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter-set placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class PlaceCountersOnTargetsIntent:
    actor: str
    object_refs: tuple[str, ...]
    maximum_targets: int
    counter_name: str
    amount: int | Literal["existing_named_counter_count"]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        refs = tuple(self.object_refs)
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if any(
            type(value) is not str or not value
            for value in (self.actor, normalized, self.reason)
        ):
            raise ValueError(
                "Counter-target intents require actor, counter, and reason"
            )
        if (
            type(self.maximum_targets) is not int
            or self.maximum_targets <= 0
            or len(refs) > self.maximum_targets
            or any(type(ref) is not str or not ref for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError(
                "Counter-target intents require unique refs within a positive maximum"
            )
        if self.amount != "existing_named_counter_count" and (type(self.amount) is not int or self.amount <= 0):
            raise ValueError(
                "Counter-target intent amount must be a positive exact integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Counter-target intent source must be a nonempty reference"
            )
        object.__setattr__(self, "object_refs", refs)
        object.__setattr__(self, "counter_name", normalized)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter-target placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class PlacePlayerCountersIntent:
    actor: str
    player_ids: tuple[str, ...]
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.player_ids, (list, tuple)):
            raise ValueError(
                "Player counter intent subjects must be an ordered sequence"
            )
        players = tuple(self.player_ids)
        if type(self.counter_name) is not str:
            raise ValueError(
                "Player counter intents require actor, counter, and reason"
            )
        normalized = " ".join(self.counter_name.casefold().split())
        if any(
            type(value) is not str or not value
            for value in (self.actor, normalized, self.reason)
        ):
            raise ValueError(
                "Player counter intents require actor, counter, and reason"
            )
        if (
            not players
            or any(type(player) is not str or not player for player in players)
            or len(players) != len(set(players))
        ):
            raise ValueError(
                "Player counter intent subjects must be unique nonempty seats"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Player counter intent amount must be a positive integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Player counter intent source must be a nonempty reference"
            )
        object.__setattr__(self, "player_ids", players)
        object.__setattr__(self, "counter_name", normalized)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Player counter placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class CounterStackIntent:
    actor: str
    stack_ref: str
    reason: str
    countered_by: str
    counter_destination: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    source_controller: str | None = None
    ability_source_loss: SemanticSourceContext | None = None
    activation_prohibition: CounterActivationProhibition | None = None

    def __post_init__(self) -> None:
        if self.activation_prohibition is not None:
            from ..stack_counter import CounterActivationProhibition

            if (not isinstance(self.activation_prohibition, CounterActivationProhibition)
                    or self.ability_source_loss is not None or self.counter_destination is not None):
                raise ValueError("The resolved activation prohibition requires one ordinary counter instruction")
        if self.ability_source_loss is not None and (
            not isinstance(self.ability_source_loss, SemanticSourceContext) or self.counter_destination is not None
        ):
            raise ValueError("Countered ability source loss requires a typed plain-counter source")
        if self.source_controller is not None and (type(self.source_controller) is not str or not self.source_controller):
            raise ValueError("Counter source controller must be a player identity")
        if self.counter_destination is not None:
            from ..zone_trigger_events import counter_destination_fields

            counter_destination_fields(self.counter_destination)
        elif self.replacement_selections:
            raise ValueError("Counter replacement selections require a represented destination")
        object.__setattr__(self, "replacement_selections",
            _freeze_replacement_selections(self.replacement_selections, family="Countered spell"))


@dataclass(frozen=True, slots=True)
class EliminatePlayersIntent:
    actor: str
    players: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class WinGameIntent:
    actor: str
    player: str


@dataclass(frozen=True, slots=True)
class CopyStackItemIntent:
    actor: str
    controller: str
    target_stack_ref: str
    targets: tuple[str, ...]
    target_groups: FrozenMap
    reason: str
    target_snapshots: FrozenMap | None = None
    copy_source: FrozenMap | None = None
    permanent_copy: PermanentSpellCopySpec | None = None

    def __post_init__(self) -> None:
        if self.permanent_copy is not None and not isinstance(self.permanent_copy, PermanentSpellCopySpec):
            raise ValueError("A permanent spell copy requires a typed exception")
        if self.copy_source is not None and not isinstance(self.copy_source,FrozenMap):object.__setattr__(self,'copy_source',FrozenMap(self.copy_source))
        if self.target_snapshots is not None and not isinstance(self.target_snapshots, FrozenMap):
            object.__setattr__(self, "target_snapshots", FrozenMap(self.target_snapshots))
        if not isinstance(self.target_groups, FrozenMap):
            object.__setattr__(
                self,
                "target_groups",
                FrozenMap(self.target_groups),
            )


@dataclass(frozen=True, slots=True)
class RetargetStackItemIntent:
    actor: str
    target_stack_ref: str
    targets: tuple[str, ...]
    target_groups: FrozenMap
    source_stack_ref: str
    collect_single_target_ward: bool = False
    target_snapshots: FrozenMap | None = None

    def __post_init__(self) -> None:
        if type(self.collect_single_target_ward) is not bool or self.collect_single_target_ward and len(self.targets) != 1:
            raise ValueError("A mandatory changed-target Ward occurrence requires one target")
        if self.target_snapshots is not None:
            if not isinstance(self.target_snapshots, Mapping) or set(self.target_snapshots) != set(self.targets):
                raise ValueError("Optional target changes require the exact selected snapshot identities")
            if not isinstance(self.target_snapshots, FrozenMap):
                object.__setattr__(self, "target_snapshots", FrozenMap(self.target_snapshots))
        if not isinstance(self.target_groups, FrozenMap):
            object.__setattr__(
                self,
                "target_groups",
                FrozenMap(self.target_groups),
            )


from .token_creation_intent import CreateTokenIntent, DistributedTokenCreationIntent


@dataclass(frozen=True, slots=True)
class CopyControlledTokensIntent:
    actor: str
    controller: str
    chosen_token_ref: str
    source_stack_ref: str
    reason: str

    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("Controlled-token copies require a supported intent version")


@dataclass(frozen=True, slots=True)
class AddSubtypeIntent:
    actor: str
    object_ref: str
    subtype: str
    source: SemanticSourceContext
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_ref,
                self.subtype,
                self.reason,
            )
        ):
            raise ValueError(
                "Zone-object subtype intents require actor, object, subtype, and reason"
            )
        if not isinstance(self.source, SemanticSourceContext):
            raise TypeError(
                "Zone-object subtype intents require typed source context"
            )
        subtype = canonical_creature_subtype(self.subtype)
        if subtype is None:
            raise ValueError(
                "Zone-object subtype intents require a pinned creature subtype"
            )
        object.__setattr__(self, "subtype", subtype.title())


@dataclass(frozen=True, slots=True)
class GrantZoneObjectKeywordIntent:
    actor: str
    object_ref: str | None
    keyword: str
    source: SemanticSourceContext
    reason: str
    controlled_creature_set: bool = False

    def __post_init__(self) -> None:
        from ..zone_object_keyword_model import zone_object_intent_keyword

        if any(type(value) is not str or not value for value in (self.actor, self.keyword, self.reason)):
            raise ValueError("Zone-object keyword intents require actor, keyword, and reason")
        if not isinstance(self.source, SemanticSourceContext):
            raise TypeError("Zone-object keyword intents require typed source context")
        keyword = zone_object_intent_keyword(
            self.object_ref, self.keyword, controlled_creature_set=self.controlled_creature_set,
        )
        object.__setattr__(self, "keyword", keyword)


@dataclass(frozen=True, slots=True)
class ProliferateSubject:
    subject_kind: Literal["player", "permanent"]
    subject_id: str
    ref: str
    counter_names: tuple[str, ...]
    logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.subject_kind) is not str or self.subject_kind not in {
            "player",
            "permanent",
        }:
            raise ValueError(
                "Proliferate subjects must be players or permanents"
            )
        if any(
            type(value) is not str or not value
            for value in (self.subject_id, self.ref)
        ):
            raise ValueError(
                "Proliferate subjects require stable IDs and references"
            )
        if not isinstance(self.counter_names, (list, tuple)) or any(
            type(value) is not str or not value
            for value in self.counter_names
        ):
            raise ValueError(
                "Proliferate counter names must be nonempty strings"
            )
        names = tuple(
            " ".join(value.casefold().split())
            for value in self.counter_names
        )
        if (
            not names
            or any(not value for value in names)
            or names != tuple(sorted(set(names)))
        ):
            raise ValueError(
                "Proliferate counter names must be unique canonical strings"
            )
        if self.subject_kind == "player":
            if self.subject_id != self.ref or self.logical_object_id is not None:
                raise ValueError(
                    "Player Proliferate subjects cannot carry object identity"
                )
        elif not self.logical_object_id:
            raise ValueError(
                "Permanent Proliferate subjects require logical identity"
            )
        object.__setattr__(self, "counter_names", names)


@dataclass(frozen=True, slots=True)
class ProliferateIntent:
    actor: str
    subjects: tuple[ProliferateSubject, ...]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    choose_phase_out: bool = False

    def __post_init__(self) -> None:
        if type(self.choose_phase_out) is not bool:
            raise ValueError("Proliferate phase-out selection must be Boolean")
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.reason)
        ):
            raise ValueError(
                "Proliferate intents require an actor and reason"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Proliferate source references must be nonempty or null"
            )
        if any(
            not isinstance(subject, ProliferateSubject)
            for subject in self.subjects
        ):
            raise ValueError(
                "Proliferate intents require typed subject snapshots"
            )
        identities = tuple(
            (subject.subject_kind, subject.subject_id)
            for subject in self.subjects
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Proliferate subject snapshots must be unique")
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Proliferate",
            ),
        )

    @property
    def selections(self) -> tuple[str, ...]:
        return tuple(subject.ref for subject in self.subjects)


@dataclass(frozen=True, slots=True)
class FlipSourcePermanentIntent:
    actor: str
    object_id: str
    incarnation: str
    card_ref: str
    reason: str

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (
            self.actor, self.object_id, self.incarnation, self.card_ref, self.reason,
        )):
            raise ValueError("A flip intent requires its original source, actor and reason")


SemanticIntent: TypeAlias = (
    FlipSourcePermanentIntent |
    DrawCardsIntent | ControlIntent
    | QueueReflexiveTriggerIntent
    | QueueRevealUntilReflexiveIntent
    | MillCardsIntent
    | BecomeMonarchIntent
    | VentureProgressIntent
    | TakeInitiativeIntent
    | SetPermanentTappedIntent
    | ProhibitNextUntapIntent
    | ProhibitNextPlayerUntapIntent
    | SetPermanentsTappedIntent
    | UntapAllCreaturesIntent
    | CreateRegenerationShieldIntent
    | CreateRegenerationShieldSetIntent
    | DestroyPermanentIntent
    | CompositionIntent
    | DestroyPermanentSetIntent
    | DestroyPermanentTargetsIntent
    | ReturnPermanentToOwnerHandIntent
    | ReturnGraveyardCardToOwnerHandIntent
    | ExilePermanentIntent
    | ExilePublicGraveyardCardIntent
    | MovePublicZoneSetIntent
    | DealFixedDamageSetIntent
    | AddManaIntent
    | PaidDepartureDamageIntent
    | SetCardDesignationIntent
    | BecomeMonstrousIntent
    | BecomeRenownedIntent
    | PrepareSourceIntent
    | PairSoulbondIntent
    | RecordChoiceIntent
    | ConniveCompletedIntent
    | BeginRingTemptationIntent
    | CreateCompiledEmblemIntent
    | CompleteRingBearerIntent
    | ChampionExileIntent
    | EncodeCipherIntent
    | ReturnLinkedAbilityExileIntent
    | ZoneMoveIntent
    | MoveObjectsSimultaneouslyIntent
    | MadnessChoiceIntent
    | MiracleCastChoiceIntent
    | DiscoverCastIntent
    | ResolvingExileLandPlayIntent
    | ResolvingSharedTypeLibraryPlayIntent
    | ResolveQualifiedLibraryCastIntent
    | RevealCapturedDrawsIntent
    | ResolveCapturedDrawCastIntent
    | ResolveBoundedHandCastIntent
    | ResolveRippleCastIntent
    | RippleBottomIntent
    | GraveyardCastIntent
    | ExileGraveyardCopiesIntent
    | DiscoverCleanupIntent
    | ChooseOneRestBottomRandomIntent
    | ShuffleLibraryIntent
    | ReturnCardsToLibraryTopIntent
    | RecordZoneMoveIntent
    | LifeChangeIntent
    | PayLifeIntent
    | PayEnergyIntent
    | RevealLibraryCardsIntent
    | RevealEachLibraryTopIntent
    | InspectHandIntent
    | RevealColoredHandCardsIntent
    | MoveLibraryCardsToBottomIntent
    | ScryLibraryIntent
    | SurveilLibraryIntent
    | LibrarySelectionIntent
    | ReorderLibraryTopIntent
    | PayManaCostIntent
    | PlaceCountersIntent
    | RemoveCountersIntent
    | CounterMovementIntent
    | RemoveAllCountersIntent
    | PlaceCounterBatchIntent
    | PlaceCountersOnSetIntent
    | PlaceCountersOnTargetsIntent
    | PlacePlayerCountersIntent
    | CounterStackIntent
    | EliminatePlayersIntent
    | WinGameIntent
    | CopyStackItemIntent
    | RetargetStackItemIntent
    | CreateTokenIntent
    | DistributedTokenCreationIntent
    | CopyControlledTokensIntent
    | AddSubtypeIntent
    | GrantZoneObjectKeywordIntent
    | ProliferateIntent
    | DomainEffectIntent
    | DiscardWholeHandsIntent
    | DiscardWholeHandsResultIntent
    | HandLibraryRedrawIntent
    | RandomDiscardIntent
    | RandomGraveyardReturnIntent
    | GraveyardRecyclingIntent
    | TargetLandTypeSettingIntent
    | FixedSourceSacrificeIntent
    | TemporaryCastRestrictionIntent | TemporaryDrawRedirectIntent | TargetCombatDamageMultiplierIntent | ScheduleNextDrawResultIntent | NoMaximumForGameIntent
    | TemporaryProtectedAttackPaymentIntent
    | NextTurnAttackRestrictionIntent | CounterBoundAttackRestrictionIntent
    | ScheduleTurnSkipIntent
    | ScheduleExtraCombatIntent
    | ScheduleAdditionalBeginningIntent
    | TokenMillRepeatIntent
    | EarthbendIntent
    | FinishEarthbendIntent
    | ReturnEarthbentObjectsIntent
    | SuspectIntent
    | CombatRemovalIntent
    | ProhibitLibrarySearchIntent
    | AscendIntent
)
ResultShape: TypeAlias = Literal["single", "by_player"]


@dataclass(frozen=True, slots=True)
class IntentPlan:
    operation: str
    handler_id: str
    intents: tuple[SemanticIntent, ...]
    result_shape: ResultShape = "single"

    def __post_init__(self) -> None:
        if self.result_shape not in {"single", "by_player"}:
            raise ValueError(f"Unknown intent result shape {self.result_shape!r}")
        if self.result_shape == "single" and len(self.intents) != 1:
            raise ValueError("A single-result plan must contain one intent")
