"""Apply one temporary keyword to the retained public creature incarnations."""

from ..continuous_effect_model import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from ..errors import GameRuleError
from ..rules.main_phase_creature_cohort import current_creature_cohort_grant


def apply_creature_cohort_grant(host, effect, *, actor, reason):
    try:
        targets, keywords = current_creature_cohort_grant(host, effect, actor=actor)
        if not targets:
            return ()
        source = ResolutionEffectSource.from_effect(effect)
        create_resolution_continuous_effect_components(
            host, source=source, targets=targets,
            components=(ResolutionContinuousComponent(
                Layer.ABILITY, "6", tuple(ContinuousOperation("add_ability", keyword) for keyword in keywords),
            ),),
        )
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    refs = tuple(card.ref for card in targets)
    host._log(actor, "effect.mass_modify", f"The retained creatures gain {keywords[0].lower()} until end of turn.",
              {"objects": list(refs), "keywords": list(keywords), "reason": reason}, importance=1,
              changed_objects=[card.object_id for card in targets])
    return refs
