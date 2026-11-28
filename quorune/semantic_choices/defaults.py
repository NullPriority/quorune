from __future__ import annotations

from functools import lru_cache

from ..discover_model import (NONLAND_SCAN_CAPABILITY, NONLAND_SCAN_OPERATION,
                              NONLAND_SCAN_STEP_OPERATION, NONLAND_SCAN_CAST_OPERATION)
from .artifact_search_exchange import ArtifactSearchExchangeChoiceHandler
from .library_creature_arrival import LibraryCreatureArrivalChoiceHandler
from .registry import SemanticChoiceRegistry
from ..rules.bargain_search import BARGAIN_SEARCH_CAPABILITIES, BARGAIN_SEARCH_CAST_OPERATION
from .composition_order import CompositionOrderHandler
from .control_recipient import ControlRecipientChoiceHandler
from .target_land_type import TargetLandTypeChoiceHandler
from .chosen_counter_removal import ChosenCounterRemovalHandler
from .chosen_counter_placement import ChosenCounterPlacementHandler
from .captured_draw_keyword_grant import CapturedCounterKeywordGrantHandler, CapturedDrawKeywordGrantHandler
from .mutate import MutateMergeChoiceHandler
from .created_token_counters import CREATED_TOKEN_ATTACHMENT_HANDLERS, CREATED_TOKEN_COUNTER_HANDLERS
from .created_entry_aura_attachment import CREATED_ENTRY_AURA_ATTACHMENT_HANDLERS
from .incubate import INCUBATE_HANDLERS
from .investigate import SequentialInvestigateHandler
from .endure import SourceEndureChoiceHandler
from .departure_x_payment import DepartingXPaymentHandler
from .name_sticker import NameStickerChoiceHandler
from .budgeted_resolving_cast import BudgetedHandGraveyardCastHandler
from .blight import FixedBlightChoiceHandler
from .discover import DiscoverScanHandler, QUALIFIED_SCAN_HANDLERS
from .stack_counter_name_scan import STACK_COUNTER_NAME_SCAN_HANDLERS
from .discover_cast import DiscoverCastChoiceHandler
from .fixed_top_exile import FIXED_TOP_EXILE_HANDLERS
from .hideaway_play import HideawayPlayChoiceHandler
from .exile_until_same_name import REPEATED_LIBRARY_EXILE_HANDLERS
from .variable_library_selection import VariableLibrarySelectionHandler
from .public_library_piles import PublicLibraryPilesHandler
from .reveal_until_library_selection import RevealUntilLibrarySelectionHandler
from .one_revealed_card_placement import ONE_REVEALED_CARD_PLACEMENT_HANDLERS
from .revealed_creature_statistics import RevealedCreatureStatisticsHandler
from .extort import ExtortChoiceHandler
from .mana_contribution_draw import ManaContributionDrawHandler
from .secret_number import SecretNumberExtremesHandler
from .announced_permanent_reveal import AnnouncedPermanentRevealHandler
from .target_permanent_shuffle_reveal import TargetPermanentShuffleRevealHandler
from .opponent_library_shuffle import OpponentShuffleTopPartitionHandler
from .fame_fortune import FameFortuneChoiceHandler
from .target_controller_library_reveal import TargetControllerLibraryRevealHandler
from .actual_life_transfer import ActualLifeTransferHandler
from .artifact_exchange import ARTIFACT_EXCHANGE_CHOICE_HANDLERS
from .temporary_copy_subject import TemporaryCopySubjectChoiceHandler
from .temporary_occurrence_copy import TemporaryOccurrenceCopyChoiceHandler
from .object_selection import OBJECT_SELECTION_HANDLERS
from .hand_entry_collections import PrivateHandCollectionHandler
from .cast_x_search_choice import CastXZoneSearchChoiceHandler
from .named_zone_search import NamedZoneSearchChoiceHandler
from .target_library_inspection import TargetLibraryInspectionHandler
from .shared_type_library import SharedTypeLibraryHandler
from .library_and_hand import LIBRARY_AND_HAND_CHOICE_HANDLERS
from .ordering import ORDERING_CHOICE_HANDLERS
from .fixed_top_order import FIXED_TOP_ORDER_HANDLERS
from .owner_library_position_choice import OwnerLibraryPositionChoiceHandler
from .targeted_graveyard_cast import TargetedGraveyardCastChoiceHandler
from .discarded_card_cast import DiscardedCardCastChoiceHandler
from .graveyard_copy_cast import GRAVEYARD_COPY_HANDLERS
from .untargeted_untap import FixedLandUntapChoiceHandler
from .tap_attack_result import TapAttackResultChoiceHandler
from .scry import SCRY_CHOICE_HANDLERS
from .surveil import SURVEIL_CHOICE_HANDLERS
from .fixed_library_selection import (
    FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS,
)
from .payments import PAYMENT_CHOICE_HANDLERS
from .public_selection import PUBLIC_SELECTION_CHOICE_HANDLERS
from .scalar import SCALAR_CHOICE_HANDLERS
from .stack_targets import STACK_TARGET_CHOICE_HANDLERS
from .token_and_copy import TOKEN_AND_COPY_CHOICE_HANDLERS
from .role_tokens import ROLE_TOKEN_CHOICE_HANDLERS
from .damage_dealer_token import DamageDealerTokenChoiceHandler
from .attached_role_batch import AttachedRoleBatchChoiceHandler
from .manifest import MANIFEST_CHOICE_HANDLERS
from .cloak_library import CLOAK_CHOICE_HANDLERS
from .damage_prevention import DAMAGE_PREVENTION_CHOICE_HANDLERS
from .conditional_draw import CONDITIONAL_DRAW_CHOICE_HANDLERS
from .resolution_condition import PUBLIC_RESOLUTION_CONDITION_HANDLERS
from .ordered_type_sacrifice import OrderedTypeSacrificeHandler
from .event_counter_threshold import EventCounterThresholdHandler
from .life_gain_counter_reset import LifeGainCounterResetHandler
from .stack_counter_followup import STACK_COUNTER_FOLLOWUP_HANDLERS
from .optional_draw import OPTIONAL_DRAW_CHOICE_HANDLERS
from .optional_counter_placement import (
    OPTIONAL_COUNTER_PLACEMENT_CHOICE_HANDLERS,
)
from .optional_effect import OPTIONAL_EFFECT_CHOICE_HANDLERS
from .death_return import DEATH_RETURN_CHOICE_HANDLERS
from .self_counter_keyword_actions import (
    SELF_COUNTER_KEYWORD_ACTION_HANDLERS,
)
from .bolster import BOLSTER_CHOICE_HANDLERS
from .modular import MODULAR_CHOICE_HANDLERS
from .fixed_counter_movement import FixedCounterMovementHandler
from .departure_counter_transfer import DepartureCounterTransferHandler
from .controlled_arrival_proliferate import ControlledArrivalProliferateHandler
from .source_counter_distribution import SourceCounterDistributionHandler
from .graft import GraftCounterMovementHandler
from .amass import AMASS_CHOICE_HANDLERS
from .apnap_commit import APNAP_COMMIT_CHOICE_HANDLERS
from .chosen_attack_turn import ChosenAttackTurnHandler
from .madness import MADNESS_CHOICE_HANDLERS
from .miracle import MiracleCastChoiceHandler
from .attacking_tokens import ATTACKING_TOKEN_CHOICE_HANDLERS
from .fixed_attacking_tokens import FixedAttackingTokenDestinationHandler
from .targeted_attack_die_copy import TargetedAttackDieCopyHandler
from .hand_inspection import HAND_INSPECTION_CHOICE_HANDLERS
from .connive import FixedConniveChoiceHandler
from .exploit import ExploitChoiceHandler
from .soulbond import SoulbondPairChoiceHandler
from .ring import RingTemptationChoiceHandler
from .champion import ChampionExileChoiceHandler, ChampionReturnChoiceHandler
from .cipher import CipherEncodingChoiceHandler
from .discard_results import DiscardResultDrawHandler
from .hand_library_redraw import HandLibraryRedrawHandler
from .fixed_hand_putback import FixedHandPutbackHandler
from .linked_hand_mana import LinkedHandImprintChoice
from .colored_hand_reveals import ColoredHandRevealChoiceHandler
from .graveyard_return import GraveyardReturnChoiceHandler
from .targeted_graveyard_exile import TargetedGraveyardExileChoiceHandler
from .qualified_library_cast import QualifiedLibraryCastChoice
from .keep_permanents_by_type import KeepPermanentsByTypeHandler


def _nonland_exile_handlers():
    return (
        DiscoverScanHandler(
            NONLAND_SCAN_OPERATION, 'choice.library.exile-until-nonland.v1',
            rule_references=('400.7j', '608.2c', '701.13a'),
            capability_dependencies=(NONLAND_SCAN_CAPABILITY,),
            replay_fixture='first-nonland-exile-and-optional-cast',
        ),
        DiscoverScanHandler(
            NONLAND_SCAN_STEP_OPERATION, 'choice.library.exile-until-nonland-step.v1',
            rule_references=('400.7j', '608.2c', '701.13a'),
            capability_dependencies=(NONLAND_SCAN_CAPABILITY,),
            replay_fixture='first-nonland-exile-and-optional-cast',
        ),
        DiscoverCastChoiceHandler(
            NONLAND_SCAN_CAST_OPERATION, 'choice.library.exile-until-nonland-cast.v1',
            rule_references=('400.7j', '608.2d', '608.2g'),
            capability_dependencies=(NONLAND_SCAN_CAPABILITY,),
            mutation_path=('DiscoverCastIntent',),
            replay_fixture='first-nonland-exile-and-optional-cast',
        ),
    )


@lru_cache(maxsize=1)
def default_semantic_choice_registry() -> SemanticChoiceRegistry:
    return SemanticChoiceRegistry(
        (
            ExtortChoiceHandler(),
            ManaContributionDrawHandler(),
            SecretNumberExtremesHandler(),
            AnnouncedPermanentRevealHandler(),
            FixedAttackingTokenDestinationHandler(),
            TargetedAttackDieCopyHandler(),
            TargetPermanentShuffleRevealHandler(),
            OpponentShuffleTopPartitionHandler(),
            FameFortuneChoiceHandler(),
            TargetControllerLibraryRevealHandler(),
            CompositionOrderHandler(),
            MutateMergeChoiceHandler(),
            ActualLifeTransferHandler(),
            TemporaryCopySubjectChoiceHandler(),
            TemporaryOccurrenceCopyChoiceHandler(),
            DiscardResultDrawHandler(),
            HandLibraryRedrawHandler(),
            FixedHandPutbackHandler(),
            LinkedHandImprintChoice(),
            ColoredHandRevealChoiceHandler(),
            GraveyardReturnChoiceHandler(),
            TargetedGraveyardExileChoiceHandler(),
            QualifiedLibraryCastChoice(),
            *CREATED_TOKEN_COUNTER_HANDLERS,
            *CREATED_TOKEN_ATTACHMENT_HANDLERS,
            *CREATED_ENTRY_AURA_ATTACHMENT_HANDLERS,
            *INCUBATE_HANDLERS,
            SequentialInvestigateHandler(),
            SourceEndureChoiceHandler(),
            DepartingXPaymentHandler(),
            NameStickerChoiceHandler(),
            BudgetedHandGraveyardCastHandler(),
            FixedBlightChoiceHandler(),
            DiscoverScanHandler(),
            DiscoverScanHandler('discover_step','choice.library.discover-step.v1'),
            DiscoverCastChoiceHandler(),
            DiscoverCastChoiceHandler('discover_finish','choice.library.discover-finish.v1'),
            DiscoverCastChoiceHandler(
                BARGAIN_SEARCH_CAST_OPERATION, 'choice.library.bargained-private-search-cast.v1',
                rule_references=('202.3', '400.7', '406.3', '608.2g', '702.166a'),
                capability_dependencies=BARGAIN_SEARCH_CAPABILITIES,
                private_data=('searched exile incarnation', 'cast options'),
                mutation_path=('DiscoverCastIntent', 'ZoneMoveIntent'),
                replay_fixture='bargained-private-search-resolving-cast',
                test_modules=('tests.test_bargain_private_search',),
            ),
            *_nonland_exile_handlers(),
            *QUALIFIED_SCAN_HANDLERS,
            *STACK_COUNTER_NAME_SCAN_HANDLERS,
            *FIXED_TOP_EXILE_HANDLERS,
            HideawayPlayChoiceHandler(),
            *REPEATED_LIBRARY_EXILE_HANDLERS,
            VariableLibrarySelectionHandler(),
            RevealUntilLibrarySelectionHandler(),
            *ONE_REVEALED_CARD_PLACEMENT_HANDLERS,
            RevealedCreatureStatisticsHandler(),
            *ARTIFACT_EXCHANGE_CHOICE_HANDLERS,
            ArtifactSearchExchangeChoiceHandler(),
            LibraryCreatureArrivalChoiceHandler(),
            *SCALAR_CHOICE_HANDLERS,
            *OBJECT_SELECTION_HANDLERS,
            PrivateHandCollectionHandler(),
            NamedZoneSearchChoiceHandler(),
            CastXZoneSearchChoiceHandler(),
            TargetLibraryInspectionHandler(),
            SharedTypeLibraryHandler(),
            *LIBRARY_AND_HAND_CHOICE_HANDLERS,
            *ORDERING_CHOICE_HANDLERS,
            *FIXED_TOP_ORDER_HANDLERS,
            OwnerLibraryPositionChoiceHandler(),
            TargetedGraveyardCastChoiceHandler(),
            DiscardedCardCastChoiceHandler(),
            *GRAVEYARD_COPY_HANDLERS,
            FixedLandUntapChoiceHandler(),
            TapAttackResultChoiceHandler(),
            ControlRecipientChoiceHandler(),
            TargetLandTypeChoiceHandler(),
            ChosenCounterRemovalHandler(),
            ChosenCounterPlacementHandler(),
            CapturedDrawKeywordGrantHandler(),
            CapturedCounterKeywordGrantHandler(),
            *SCRY_CHOICE_HANDLERS,
            *SURVEIL_CHOICE_HANDLERS,
            *FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS,
            PublicLibraryPilesHandler(),
            *PAYMENT_CHOICE_HANDLERS,
            *PUBLIC_SELECTION_CHOICE_HANDLERS,
            *STACK_TARGET_CHOICE_HANDLERS,
            *TOKEN_AND_COPY_CHOICE_HANDLERS,
            *ROLE_TOKEN_CHOICE_HANDLERS,
            DamageDealerTokenChoiceHandler(),
            AttachedRoleBatchChoiceHandler(),
            *MANIFEST_CHOICE_HANDLERS,
            *CLOAK_CHOICE_HANDLERS,
            *DAMAGE_PREVENTION_CHOICE_HANDLERS,
            *CONDITIONAL_DRAW_CHOICE_HANDLERS,
            *PUBLIC_RESOLUTION_CONDITION_HANDLERS,
            OrderedTypeSacrificeHandler(),
            EventCounterThresholdHandler(),
            LifeGainCounterResetHandler(),
            *STACK_COUNTER_FOLLOWUP_HANDLERS,
            *OPTIONAL_DRAW_CHOICE_HANDLERS,
            *OPTIONAL_COUNTER_PLACEMENT_CHOICE_HANDLERS,
            *OPTIONAL_EFFECT_CHOICE_HANDLERS,
            *DEATH_RETURN_CHOICE_HANDLERS,
            *SELF_COUNTER_KEYWORD_ACTION_HANDLERS,
            *BOLSTER_CHOICE_HANDLERS,
            *MODULAR_CHOICE_HANDLERS,
            GraftCounterMovementHandler(),
            FixedCounterMovementHandler(),
            DepartureCounterTransferHandler(),
            ControlledArrivalProliferateHandler(),
            SourceCounterDistributionHandler(),
            *AMASS_CHOICE_HANDLERS,
            KeepPermanentsByTypeHandler(),
            *APNAP_COMMIT_CHOICE_HANDLERS,
            ChosenAttackTurnHandler(),
            *MADNESS_CHOICE_HANDLERS,
            MiracleCastChoiceHandler(),
            *ATTACKING_TOKEN_CHOICE_HANDLERS,
            *HAND_INSPECTION_CHOICE_HANDLERS,
            FixedConniveChoiceHandler(),
            ExploitChoiceHandler(),
            SoulbondPairChoiceHandler(),
            RingTemptationChoiceHandler(),
            ChampionExileChoiceHandler(),
            CipherEncodingChoiceHandler(),
            ChampionReturnChoiceHandler(),
        )
    ).freeze()
