from __future__ import annotations

"""Explicit graveyard sources grant independently compiled recipient abilities."""

from dataclasses import dataclass
from ..continuous_conditions import (
    GRAVEYARD_TYPED_GRANT_CAPABILITY_ID, GRAVEYARD_QUERY_GRANT_HANDLER_ID,
    GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID,
)
from .continuous_components import FixedQueryAbilityGrantHandler
from .conditional_continuous import FixedPublicStateGrantedAbilityHandler
from .context import SemanticNodeError


@dataclass(frozen=True, slots=True)
class GraveyardQueryAbilityGrantHandler(FixedQueryAbilityGrantHandler):
    handler_id: str = GRAVEYARD_QUERY_GRANT_HANDLER_ID
    family: str = 'continuous.ability.graveyard_query_grant'
    capability_dependencies: tuple[str, ...] = (
        GRAVEYARD_TYPED_GRANT_CAPABILITY_ID, 'continuous.ability.graveyard_static_grant',
        'continuous.ability.fixed_query_grant',
    )


@dataclass(frozen=True, slots=True)
class GraveyardConditionalAbilityGrantHandler(FixedPublicStateGrantedAbilityHandler):
    handler_id: str = GRAVEYARD_CONDITIONAL_GRANT_HANDLER_ID
    family: str = 'continuous.ability.graveyard_conditional_grant'
    capability_dependencies: tuple[str, ...] = (
        GRAVEYARD_TYPED_GRANT_CAPABILITY_ID, 'continuous.ability.graveyard_static_grant',
        'continuous.ability.fixed_public_state_grant', 'continuous.characteristics.fixed_public_state',
    )

    def validate(self, descriptor):
        node = super(GraveyardConditionalAbilityGrantHandler, self).validate(descriptor)
        if node.target_kind != 'fixed_query':
            raise SemanticNodeError('Graveyard conditional grants need a live battlefield recipient query')
        return node
