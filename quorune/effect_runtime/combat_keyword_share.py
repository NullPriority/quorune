"""Commit the shared keyword snapshot through the existing layer-six journal."""

from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionEffectSource, create_resolution_continuous_effect, matching_battlefield_objects,
)
from ..errors import GameRuleError
from ..rules.combat_keyword_share import (
    combat_keyword_share_effect_is_exact, combat_keyword_share_query, shared_creature_keywords,
)


def apply_combat_keyword_share(host, effect, *, actor):
    instruction = {field: value for field, value in effect.items() if field != "_runtime_source"}
    if (not combat_keyword_share_effect_is_exact(instruction, controller=actor)
            or host.state.continuous_effects is None or actor not in host.active_seats):
        raise GameRuleError("The combat keyword share requires its exact resolving controller and typed journal")
    try:
        source = ResolutionEffectSource.from_effect(effect)
        affected = matching_battlefield_objects(host, combat_keyword_share_query(actor))
        keywords = shared_creature_keywords(host, affected)
        if not keywords:
            return []
        create_resolution_continuous_effect(
            host, source=source, targets=affected, layer=Layer.ABILITY, sublayer="6",
            operations=tuple(ContinuousOperation("add_ability", keyword) for keyword in keywords),
        )
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    return [creature.object_id for creature in affected]
