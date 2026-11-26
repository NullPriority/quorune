"""Apply one temporary keyword effect to the retained battlefield incarnations."""

from ..continuous_effect_model import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from ..errors import GameRuleError
from ..rules.captured_draw_keyword_grant import decode_retained_keyword_grant


def apply_captured_draw_keyword_grant(host, effect, *, actor, reason):
    try:
        subjects = decode_retained_keyword_grant(effect, player=actor)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((entry for entry in host.state.stack if entry.ref == source.stack_ref), None)
        if (actor not in host.active_seats or item is None or item.controller != actor
                or item.kind not in {"spell", "spell_copy"} or not item.context.get("currently_resolving")):
            raise ValueError("Retained keyword grant requires its original resolving spell")
        targets = tuple(
            card for subject in subjects
            if (card := host.state.cards.get(subject.object_id)) is not None
            and card.logical_object_id == subject.logical_object_id and card.zone == "battlefield"
            and not card.phased_out
        )
        if not targets:
            return ()
        create_resolution_continuous_effect_components(
            host, source=source, targets=targets,
            components=(ResolutionContinuousComponent(
                Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Indestructible"),),
            ),),
        )
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    refs = tuple(card.ref for card in targets)
    host._log(actor, "permanent.characteristics", "The sampled creatures gain indestructible until end of turn.",
              {"objects": list(refs), "reason": reason}, importance=1,
              changed_objects=[card.object_id for card in targets])
    return refs
