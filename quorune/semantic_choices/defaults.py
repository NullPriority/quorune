from __future__ import annotations

from functools import lru_cache

from .registry import SemanticChoiceRegistry
from .artifact_exchange import ARTIFACT_EXCHANGE_CHOICE_HANDLERS
from .object_selection import OBJECT_SELECTION_HANDLERS
from .library_and_hand import LIBRARY_AND_HAND_CHOICE_HANDLERS
from .ordering import ORDERING_CHOICE_HANDLERS
from .scry import SCRY_CHOICE_HANDLERS
from .surveil import SURVEIL_CHOICE_HANDLERS
from .payments import PAYMENT_CHOICE_HANDLERS
from .public_selection import PUBLIC_SELECTION_CHOICE_HANDLERS
from .scalar import SCALAR_CHOICE_HANDLERS
from .stack_targets import STACK_TARGET_CHOICE_HANDLERS
from .token_and_copy import TOKEN_AND_COPY_CHOICE_HANDLERS
from .damage_prevention import DAMAGE_PREVENTION_CHOICE_HANDLERS
from .conditional_draw import CONDITIONAL_DRAW_CHOICE_HANDLERS
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
from .amass import AMASS_CHOICE_HANDLERS
from .apnap_commit import APNAP_COMMIT_CHOICE_HANDLERS


@lru_cache(maxsize=1)
def default_semantic_choice_registry() -> SemanticChoiceRegistry:
    return SemanticChoiceRegistry(
        (
            *ARTIFACT_EXCHANGE_CHOICE_HANDLERS,
            *SCALAR_CHOICE_HANDLERS,
            *OBJECT_SELECTION_HANDLERS,
            *LIBRARY_AND_HAND_CHOICE_HANDLERS,
            *ORDERING_CHOICE_HANDLERS,
            *SCRY_CHOICE_HANDLERS,
            *SURVEIL_CHOICE_HANDLERS,
            *PAYMENT_CHOICE_HANDLERS,
            *PUBLIC_SELECTION_CHOICE_HANDLERS,
            *STACK_TARGET_CHOICE_HANDLERS,
            *TOKEN_AND_COPY_CHOICE_HANDLERS,
            *DAMAGE_PREVENTION_CHOICE_HANDLERS,
            *CONDITIONAL_DRAW_CHOICE_HANDLERS,
            *OPTIONAL_DRAW_CHOICE_HANDLERS,
            *OPTIONAL_COUNTER_PLACEMENT_CHOICE_HANDLERS,
            *OPTIONAL_EFFECT_CHOICE_HANDLERS,
            *DEATH_RETURN_CHOICE_HANDLERS,
            *SELF_COUNTER_KEYWORD_ACTION_HANDLERS,
            *BOLSTER_CHOICE_HANDLERS,
            *MODULAR_CHOICE_HANDLERS,
            *AMASS_CHOICE_HANDLERS,
            *APNAP_COMMIT_CHOICE_HANDLERS,
        )
    ).freeze()
