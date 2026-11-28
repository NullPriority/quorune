"""Apply one temporary keyword effect to the retained battlefield incarnations."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components,
)
from ..errors import GameRuleError
from ..rules.captured_counter_keyword_grant import (
    CAPTURED_COUNTER_FLYING_VERSION, CAPTURED_COUNTER_KEYWORD_GRANT_FIELD,
    CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION, resolving_legendary_counter_keyword,
)
from ..rules.captured_draw_keyword_grant import CAPTURED_DRAW_KEYWORD_GRANT_FIELD, decode_retained_keyword_grant


def apply_captured_draw_keyword_grant(host, effect, *, actor, reason):
    counter = CAPTURED_COUNTER_KEYWORD_GRANT_FIELD in effect
    field = CAPTURED_COUNTER_KEYWORD_GRANT_FIELD if counter else CAPTURED_DRAW_KEYWORD_GRANT_FIELD
    payload = effect[field]
    if counter and isinstance(payload, Mapping) and payload.get("schema_version") == CAPTURED_COUNTER_FLYING_VERSION:
        keyword, version = "Flying", CAPTURED_COUNTER_FLYING_VERSION
        kinds = {"activated_ability", "activated_ability_copy"}
        next_turn = actor
        expiry = "until their controller's next turn"
    else:
        keyword, version = ("Vigilance" if counter else "Indestructible"), 1
        if counter and isinstance(payload, Mapping) and payload.get("schema_version") == CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION:
            version = CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION
        kinds = {"triggered_ability", "triggered_ability_copy"} if counter else {"spell", "spell_copy"}
        next_turn, expiry = None, "until end of turn"
    try:
        subjects = decode_retained_keyword_grant(effect, player=actor, field=field, version=version)
        source = ResolutionEffectSource.from_effect(effect)
        item = next((entry for entry in host.state.stack if entry.ref == source.stack_ref), None)
        if (actor not in host.active_seats or item is None or item.controller != actor
                or item.kind not in kinds or not item.context.get("currently_resolving")):
            raise ValueError("Retained keyword grant requires its original resolving stack object")
        if version == CAPTURED_LEGENDARY_COUNTER_KEYWORD_VERSION:
            keyword = resolving_legendary_counter_keyword(host, item)
        targets = tuple(
            card for subject in subjects
            if (card := host.state.cards.get(subject.object_id)) is not None
            and card.logical_object_id == subject.logical_object_id and card.zone == "battlefield"
            and not card.phased_out
        )
        if not targets:
            return ()
        create_resolution_continuous_effect_components(
            host, source=source, targets=targets, expires_on_next_turn_of=next_turn,
            components=(ResolutionContinuousComponent(
                Layer.ABILITY, "6", (ContinuousOperation("add_ability", keyword),),
            ),),
        )
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    refs = tuple(card.ref for card in targets)
    host._log(actor, "permanent.characteristics", f"The sampled creatures gain {keyword.casefold()} {expiry}.",
              {"objects": list(refs), "reason": reason}, importance=1,
              changed_objects=[card.object_id for card in targets])
    return refs
