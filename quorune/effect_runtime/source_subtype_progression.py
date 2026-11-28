"""Resolve the original subtype test and commit the locked layer components."""

from ..continuous_effect_model import ContinuousEffectDuration, ContinuousOperation, Layer
from ..continuous_effect_state import ResolutionContinuousComponent, ResolutionEffectSource, create_resolution_continuous_effect_components
from ..errors import GameRuleError
from ..rules.source_subtype_progression import progression_parts, ring_damage_outcome_program_dependencies


def apply_source_subtype_progression(host, effect, *, actor, reason):
    try:
        frame = ResolutionEffectSource.from_effect(effect)
        data = {key: value for key, value in effect.items() if key not in {"_runtime_source", "reason"}}
        stage, fragment = progression_parts(data, materialized=True)
        card = host.state.cards.get(frame.object_id)
        if (card is None or data["card"] is None or card.zone != "battlefield" or card.phased_out
                or card.logical_object_id != frame.logical_object_id or card.ref != data["card"]):
            return ()
        _types, subtypes, _supertypes = host._effective_type_parts(card)
        previous = "citizen" if stage == "scout" else "scout"
        if previous not in subtypes:
            return ()

        layer_four = ResolutionContinuousComponent(
            Layer.TYPE, "4",
            (ContinuousOperation("set_creature_subtypes", ("halfling", "scout" if stage == "scout" else "rogue")),),
        )
        if fragment is not None:
            child = host.semantics.get(fragment.semantic_key)
            if child is None or not host.semantic_program_is_current_trusted(child):
                raise ValueError("The Rogue stage requires its current trusted combat-damage child")
            required = ring_damage_outcome_program_dependencies(child)
            if not required or not set(required) <= set(child.capability_dependencies) or child.ability_id != fragment.ability_id:
                raise ValueError("The Rogue stage has an invalid combat-damage child")
            layer_six = ResolutionContinuousComponent(
                Layer.ABILITY, "6", (ContinuousOperation("add_ability_fragment", data["ability_fragments"][0]),),
            )
            components = (layer_four, layer_six)
        else:
            components = (
                layer_four,
                ResolutionContinuousComponent(Layer.ABILITY, "6", (ContinuousOperation("add_ability", "Lifelink"),)),
                ResolutionContinuousComponent(Layer.POWER_TOUGHNESS, "7b", (ContinuousOperation("set_power_toughness", (2, 3)),)),
            )
        applied = create_resolution_continuous_effect_components(
            host, source=frame, targets=(card,), components=components, duration=ContinuousEffectDuration.ZONE_OBJECT,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if applied:
        host._log(actor, "effect.characteristics.progressed", reason, {"source": card.ref, "stage": stage})
    return applied
