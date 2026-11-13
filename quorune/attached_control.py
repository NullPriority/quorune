from __future__ import annotations
from .entry_copy import copiable_oracle_id

"""Read-only layer-two composition for current static Aura control."""

from typing import Any, Collection, Sequence
from dataclasses import replace

from .continuous_effects import ContinuousEffect, Layer, order_continuous_effects
from .continuous_effect_model import ContinuousEffectOrigin,ContinuousEffectError


def static_attachment_control_effects(host: Any) -> tuple[ContinuousEffect,...]:
    from .card_programs.runtime import collect_card_program_continuous_effects
    from .ability_fragments import static_component_keys
    from .rules.attached_control import ATTACHED_CONTROL_HANDLER_ID
    from .card_programs.runtime import _annotated_static_component_keys

    def source_components(source):
        candidates=(*host.semantics.programs_for_oracle(copiable_oracle_id(source)),
            *host.semantics.runtime_handler_programs_for_oracle(copiable_oracle_id(source),active_zone='battlefield',event='characteristics.evaluate'),
            *(host.semantics.get(key) for key in _annotated_static_component_keys(source)))
        if not any(handler.get('handler_id')==ATTACHED_CONTROL_HANDLER_ID for program in candidates if program is not None for handler in program.handlers):
            return ()
        return static_component_keys(host._effective_card_data(source,maximum_layer=Layer.CONTROL).get('ability_fragments',()))

    effects=collect_card_program_continuous_effects(host.state,host.semantics,host.semantic_program_is_current_trusted,
        maximum_layer=Layer.CONTROL,static_component_resolver=source_components)
    return tuple(effect for effect in effects if effect.layer is Layer.CONTROL)


def layer_two_controller_map(
    host: Any,
    journal: Sequence[ContinuousEffect],
    static: Sequence[ContinuousEffect],
    *,
    phased_targets: Collection[str] = (),
) -> dict[str, str]:
    """Evaluate Aura-source custody first; timestamp orders dependency cycles."""
    cards = {
        card.object_id: card
        for card in host.state.cards.values()
        if card.zone == 'battlefield'
        and (not getattr(card, 'phased_out', False) or card.object_id in phased_targets)
    }
    current = {object_id: card.controller for object_id, card in cards.items()}
    effects = [
        effect for effect in journal
        if isinstance(effect, ContinuousEffect) and effect.layer is Layer.CONTROL
    ]
    effects.extend(static)
    if len({effect.effect_id for effect in effects})!=len(effects):
        raise ContinuousEffectError('Continuous effect IDs must be unique during evaluation')
    affected=set()
    for effect in effects:
        if not effect.effect_id.startswith('control-origin:'):continue
        for identity in effect.locked_objects:
            card=cards.get(identity.object_id)
            if card is not None and card.logical_object_id==identity.logical_object_id:
                current[card.object_id]=next(operation.value for operation in effect.operations if operation.op=='set_controller')
                affected.add(card.object_id)
    def targets(effect):
        return (
            (effect.related_object,)
            if effect.origin is ContinuousEffectOrigin.STATIC_ABILITY
            else effect.locked_objects
        )

    remaining=[effect for effect in effects if not effect.effect_id.startswith('control-origin:')]
    while remaining:
        has_static=any(effect.origin is ContinuousEffectOrigin.STATIC_ABILITY for effect in remaining)
        projected=control_source_dependencies(remaining,controllers=current,cards=cards) if has_static else remaining
        ordered,_cycles=order_continuous_effects(projected)
        first=ordered[0]
        # One resolution can contain disjoint recipient groups at one timestamp.
        batch=(tuple(ordered) if not has_static else (first,) if first.origin is ContinuousEffectOrigin.STATIC_ABILITY
            else tuple(effect for effect in ordered if effect.origin is not ContinuousEffectOrigin.STATIC_ABILITY
                and (effect.source_id,effect.timestamp)==(first.source_id,first.timestamp)))
        for effect in batch:
            if effect.origin is ContinuousEffectOrigin.STATIC_ABILITY:
                if effect.source_id not in cards:continue
                controller=current[effect.source_id]
            else:
                controller=next(operation.value for operation in effect.operations if operation.op=='set_controller')
            for identity in targets(effect):
                card=cards.get(identity.object_id) if identity is not None else None
                if card is not None and card.logical_object_id==identity.logical_object_id:
                    current[card.object_id] = controller
                    affected.add(card.object_id)
        applied={effect.effect_id for effect in batch}
        remaining=[effect for effect in remaining if effect.effect_id not in applied]
    return {object_id: current[object_id] for object_id in sorted(affected)}


def control_source_dependencies(effects: Sequence[ContinuousEffect],*,controllers,cards) -> tuple[ContinuousEffect,...]:
    """CR613.8a/c: only a source change that alters the pending effect matters."""
    groups={}
    for effect in effects:
        if effect.origin is not ContinuousEffectOrigin.STATIC_ABILITY:
            groups.setdefault((effect.source_id,effect.timestamp),set()).add(effect.effect_id)
    result=[]
    for effect in effects:
        if effect.origin is not ContinuousEffectOrigin.STATIC_ABILITY:
            result.append(effect);continue
        required={*effect.depends_on}
        source=cards.get(effect.source_id)
        if source is None:
            result.append(effect);continue
        for other in effects:
            if other.effect_id==effect.effect_id:continue
            identities=(*other.locked_objects,*((other.related_object,) if other.related_object is not None else ()))
            if not any((identity.object_id,identity.logical_object_id)==(source.object_id,source.logical_object_id) for identity in identities):continue
            if other.origin is ContinuousEffectOrigin.STATIC_ABILITY:
                controller=controllers.get(other.source_id);dependencies={other.effect_id}
            else:
                controller=next(operation.value for operation in other.operations if operation.op=='set_controller')
                dependencies=groups[(other.source_id,other.timestamp)]
            if controller is not None and controller!=controllers[effect.source_id]:required.update(dependencies)
        result.append(replace(effect,depends_on=tuple(sorted(required))))
    return tuple(result)
