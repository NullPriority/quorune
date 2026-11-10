from __future__ import annotations

from functools import lru_cache

from ..discover_model import (NONLAND_SCAN_CAPABILITY, NONLAND_SCAN_OPERATION,
                              NONLAND_SCAN_STEP_OPERATION, NONLAND_SCAN_CAST_OPERATION)
from .registry import SemanticChoiceRegistry
from .composition_order import CompositionOrderHandler
from .mutate import MutateMergeChoiceHandler
from .created_token_counters import CREATED_TOKEN_ATTACHMENT_HANDLERS, CREATED_TOKEN_COUNTER_HANDLERS
from .incubate import INCUBATE_HANDLERS
from .investigate import SequentialInvestigateHandler
from .endure import SourceEndureChoiceHandler
from .blight import FixedBlightChoiceHandler
from .discover import DiscoverScanHandler, QUALIFIED_SCAN_HANDLERS
from .discover_cast import DiscoverCastChoiceHandler
from .fixed_top_exile import FIXED_TOP_EXILE_HANDLERS
from .variable_library_selection import VariableLibrarySelectionHandler
from .reveal_until_library_selection import RevealUntilLibrarySelectionHandler
from .extort import ExtortChoiceHandler
from .actual_life_transfer import ActualLifeTransferHandler
from .artifact_exchange import ARTIFACT_EXCHANGE_CHOICE_HANDLERS
from .object_selection import OBJECT_SELECTION_HANDLERS
from .library_and_hand import LIBRARY_AND_HAND_CHOICE_HANDLERS
from .ordering import ORDERING_CHOICE_HANDLERS
from .fixed_top_order import FIXED_TOP_ORDER_HANDLERS
from .targeted_graveyard_cast import TargetedGraveyardCastChoiceHandler
from .graveyard_copy_cast import GRAVEYARD_COPY_HANDLERS
from .untargeted_untap import FixedLandUntapChoiceHandler
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
from .manifest import MANIFEST_CHOICE_HANDLERS
from .damage_prevention import DAMAGE_PREVENTION_CHOICE_HANDLERS
from .conditional_draw import CONDITIONAL_DRAW_CHOICE_HANDLERS
from .resolution_condition import PUBLIC_RESOLUTION_CONDITION_HANDLERS
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
from .graft import GraftCounterMovementHandler
from .amass import AMASS_CHOICE_HANDLERS
from .apnap_commit import APNAP_COMMIT_CHOICE_HANDLERS
from .madness import MADNESS_CHOICE_HANDLERS
from .miracle import MiracleCastChoiceHandler
from .attacking_tokens import ATTACKING_TOKEN_CHOICE_HANDLERS
from .fixed_attacking_tokens import FixedAttackingTokenDestinationHandler
from .hand_inspection import HAND_INSPECTION_CHOICE_HANDLERS
from .connive import FixedConniveChoiceHandler
from .exploit import ExploitChoiceHandler
from .soulbond import SoulbondPairChoiceHandler
from .ring import RingTemptationChoiceHandler
from .champion import ChampionExileChoiceHandler, ChampionReturnChoiceHandler
from .cipher import CipherEncodingChoiceHandler
from .discard_results import DiscardResultDrawHandler
from .graveyard_return import GraveyardReturnChoiceHandler


@lru_cache(maxsize=1)
def default_semantic_choice_registry() -> SemanticChoiceRegistry:
    return SemanticChoiceRegistry(
        (
            ExtortChoiceHandler(),
            FixedAttackingTokenDestinationHandler(),
            CompositionOrderHandler(),
            MutateMergeChoiceHandler(),
            ActualLifeTransferHandler(),
            DiscardResultDrawHandler(),
            GraveyardReturnChoiceHandler(),
            *CREATED_TOKEN_COUNTER_HANDLERS,
            *CREATED_TOKEN_ATTACHMENT_HANDLERS,
            *INCUBATE_HANDLERS,
            SequentialInvestigateHandler(),
            SourceEndureChoiceHandler(),
            FixedBlightChoiceHandler(),
            DiscoverScanHandler(),
            DiscoverScanHandler('discover_step','choice.library.discover-step.v1'),
            DiscoverCastChoiceHandler(),
            DiscoverCastChoiceHandler('discover_finish','choice.library.discover-finish.v1'),
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
            *QUALIFIED_SCAN_HANDLERS,
            *FIXED_TOP_EXILE_HANDLERS,
            VariableLibrarySelectionHandler(),
            RevealUntilLibrarySelectionHandler(),
            *ARTIFACT_EXCHANGE_CHOICE_HANDLERS,
            *SCALAR_CHOICE_HANDLERS,
            *OBJECT_SELECTION_HANDLERS,
            *LIBRARY_AND_HAND_CHOICE_HANDLERS,
            *ORDERING_CHOICE_HANDLERS,
            *FIXED_TOP_ORDER_HANDLERS,
            TargetedGraveyardCastChoiceHandler(),
            *GRAVEYARD_COPY_HANDLERS,
            FixedLandUntapChoiceHandler(),
            *SCRY_CHOICE_HANDLERS,
            *SURVEIL_CHOICE_HANDLERS,
            *FIXED_LIBRARY_SELECTION_CHOICE_HANDLERS,
            *PAYMENT_CHOICE_HANDLERS,
            *PUBLIC_SELECTION_CHOICE_HANDLERS,
            *STACK_TARGET_CHOICE_HANDLERS,
            *TOKEN_AND_COPY_CHOICE_HANDLERS,
            *ROLE_TOKEN_CHOICE_HANDLERS,
            *MANIFEST_CHOICE_HANDLERS,
            *DAMAGE_PREVENTION_CHOICE_HANDLERS,
            *CONDITIONAL_DRAW_CHOICE_HANDLERS,
            *PUBLIC_RESOLUTION_CONDITION_HANDLERS,
            *STACK_COUNTER_FOLLOWUP_HANDLERS,
            *OPTIONAL_DRAW_CHOICE_HANDLERS,
            *OPTIONAL_COUNTER_PLACEMENT_CHOICE_HANDLERS,
            *OPTIONAL_EFFECT_CHOICE_HANDLERS,
            *DEATH_RETURN_CHOICE_HANDLERS,
            *SELF_COUNTER_KEYWORD_ACTION_HANDLERS,
            *BOLSTER_CHOICE_HANDLERS,
            *MODULAR_CHOICE_HANDLERS,
            GraftCounterMovementHandler(),
            *AMASS_CHOICE_HANDLERS,
            *APNAP_COMMIT_CHOICE_HANDLERS,
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
