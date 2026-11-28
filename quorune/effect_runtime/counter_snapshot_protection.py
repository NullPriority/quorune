"""Apply both counter-snapshot effects before the separate Proliferate instruction."""

from ..continuous_effect_model import ContinuousOperation, Layer
from ..continuous_effect_state import (
    create_resolution_continuous_effect, matching_battlefield_objects, resolution_effect_source,
)
from ..rules.counter_snapshot_protection import KEYWORDS, counter_snapshot_protection_query
from ..damage_modifier_state import DamageModifierDuration, PreventionMode
from ..damage_prevention_creation import (
    PreventionShieldCreationRequest, PreventionSubjectAllocation,
    commit_prevention_shield_creation, plan_prevention_shield_creation,
)
from ..errors import GameRuleError


def apply_counter_snapshot_protection(host, effect, *, actor, reason):
    try:
        predicate = counter_snapshot_protection_query(effect, controller=actor)
        subjects = matching_battlefield_objects(host, predicate)
        if not subjects:
            return []
        if host.state.continuous_effects is None:
            raise ValueError("Counter snapshot protection requires the resolution-effect journal")
        source = resolution_effect_source(host, effect)
        plan = plan_prevention_shield_creation(host, PreventionShieldCreationRequest(
            source_id=source.object_id or source.stack_ref, controller=actor, mode=PreventionMode.ALL,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            subjects=tuple(PreventionSubjectAllocation(card.ref, None) for card in subjects),
            label=reason, lock_battlefield_subjects=True,
        ))
        create_resolution_continuous_effect(
            host, source=source, targets=subjects, layer=Layer.ABILITY, sublayer="6",
            operations=tuple(ContinuousOperation("add_ability", keyword) for keyword in KEYWORDS),
        )
        shields = commit_prevention_shield_creation(host, plan)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(actor, "effect.mass_modify", reason,
              {"objects": [card.ref for card in subjects], "shields": [shield.shield_id for shield in shields]},
              importance=2, changed_objects=[card.object_id for card in subjects])
    return [card.object_id for card in subjects]
