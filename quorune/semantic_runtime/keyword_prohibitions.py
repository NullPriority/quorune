from __future__ import annotations

"""Fixed opponent-creature keyword loss with CR113.11 prohibition."""

from dataclasses import dataclass
from typing import Any, Mapping
from ..continuous_effect_model import ContinuousEffect, ContinuousEffectOrigin, ContinuousOperation, Layer
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..object_predicate import ObjectQuerySpec
from .component_registry import exact_fields
from .context import SemanticNodeError


HANDLER = 'continuous.ability.opponent-keyword-prohibition.v1'
CAPABILITY = 'continuous.ability.opponent_keyword_prohibition'


@dataclass(frozen=True, slots=True)
class OpponentKeywordProhibitionHandler:
    handler_id: str = HANDLER
    schema_version: int = 1
    family: str = 'continuous.opponent_keyword_prohibition'
    event: str = 'characteristics.evaluate'
    rule_references: tuple[str, ...] = ('113.10', '113.10b', '113.10c', '113.11', '613.1f', '613.7', '613.8', '400.7')
    capability_dependencies: tuple[str, ...] = (CAPABILITY,)

    def validate(self, descriptor: Mapping[str, Any]) -> str:
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'keyword'}, field='opponent keyword prohibition')
        keyword = descriptor['keyword']
        if (descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != 1 or descriptor['event'] != self.event
                or type(keyword) is not str or keyword.title() not in FIXED_CHARACTERISTIC_KEYWORDS
                or keyword != keyword.casefold()):
            raise SemanticNodeError('Opponent keyword prohibition identity or fixed keyword changed')
        return keyword

    def lower(self, descriptor: Mapping[str, Any], context: Any) -> tuple[ContinuousEffect, ...]:
        keyword = self.validate(descriptor)
        return (ContinuousEffect(effect_id=f'{context.source_object_id}:{context.component_id}',
            source_id=context.source_object_id, layer=Layer.ABILITY, sublayer='6',
            timestamp=context.source_timestamp, operations=(ContinuousOperation('prohibit_ability', keyword),),
            origin=ContinuousEffectOrigin.STATIC_ABILITY,
            applies=ObjectQuerySpec(zones=('battlefield',), types_all=('creature',),
                excluded_controllers=(context.source_controller,))),)
