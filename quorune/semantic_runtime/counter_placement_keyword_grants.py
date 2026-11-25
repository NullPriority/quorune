"""Live layer-six grants based on the placing player's current-turn history."""

from dataclasses import dataclass, replace

from ..continuous_effect_model import ContinuousEffectRelation
from ..object_predicate import ObjectQuerySpec
from ..public_history_query_model import (
    COUNTER_PLACEMENT_HISTORY_CAPABILITY,
    PUBLIC_HISTORY_QUERY_CAPABILITY,
)
from .context import SemanticNodeError
from .continuous_components import FixedQueryKeywordGrantHandler


COUNTER_PLACEMENT_KEYWORD_GRANT_HANDLER = "continuous.ability.counter-placement-keyword-grant.v1"
_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


@dataclass(frozen=True, slots=True)
class CounterPlacementKeywordGrantHandler(FixedQueryKeywordGrantHandler):
    """Apply the existing fixed keyword grant to matching current incarnations."""

    handler_id: str = COUNTER_PLACEMENT_KEYWORD_GRANT_HANDLER
    family: str = "continuous.counter_placement_keyword_grant"
    rule_references: tuple[str, ...] = (
        "109.5", "122.6", "122.6a", "400.7", "611.3a", "611.3b", "613.1f",
    )
    capability_dependencies: tuple[str, ...] = (
        "continuous.ability.fixed_query_keyword_grant",
        PUBLIC_HISTORY_QUERY_CAPABILITY,
        COUNTER_PLACEMENT_HISTORY_CAPABILITY,
    )

    def validate(self, descriptor):
        node = super(CounterPlacementKeywordGrantHandler, self).validate(descriptor)
        if node.target_controller != "source_controller" or node.predicate != _CREATURES or node.fragments:
            raise SemanticNodeError("Counter placement grants require an ordinary controlled creature keyword grant")
        return node

    def lower(self, descriptor, context):
        effect, = super(CounterPlacementKeywordGrantHandler, self).lower(descriptor, context)
        return tuple(
            replace(
                effect, effect_id=f"{effect.effect_id}:counter:{identity.object_id}",
                relation=ContinuousEffectRelation.SOURCE_COUNTER_PLACEMENT_RECIPIENT,
                related_object=identity,
            )
            for identity in context.counter_placement_recipients
        )
