from __future__ import annotations

"""Pure source participation through the shared continuous-effect order."""

from .ability_fragments import static_component_keys
from .characteristic_read_frames import memoized_characteristic_read
from .continuous_effects import ContinuousEffectDuration, Layer
from .continuous_effect_state import active_resolution_effects
from .errors import GameRuleError
from .rules.source_speed_designations import current_source_designations


def static_component_presence_at_ability_start(host,source,program_key):
    """Apply the canonical effect prefix before a printed layer-6 source starts."""
    candidates,participants=host._static_component_candidate_plan()
    own={effect.effect_id for effect in candidates if participants.get(effect.effect_id)==(source.object_id,program_key)}
    if not own or min(effect.layer for effect in candidates if effect.effect_id in own)!=Layer.ABILITY:
        return None
    if source.zone!='battlefield':return True
    resolved={effect.effect_id:effect for card in host.state.cards.values() if card.zone=='battlefield'
        for effect in active_resolution_effects(host.state,card)}
    present=tuple(effect for effect in (*resolved.values(),*candidates)
        if effect.duration is not ContinuousEffectDuration.WHILE_SOURCE_PRESENT or effect.source_present)
    if not any(effect.effect_id in own for effect in present):return False
    ordered=memoized_characteristic_read(host,('static-source-order',),
        lambda:ordered_static_source_candidates(host,present,participants))
    prefix=[]
    for effect in ordered:
        if effect.effect_id in own:break
        producer=participants.get(effect.effect_id)
        if effect.layer==Layer.ABILITY and producer is not None:
            other=host.state.cards.get(producer[0])
            if other is not None and host._static_component_presence_at_ability_start(other,producer[1]) is False:
                continue
        prefix.append(effect)
    base=host._compiled_base_characteristics(source,host.card_record(source),error_type=GameRuleError)
    effective=host._apply_layered_characteristic_annotations(source,base,runtime_effects=tuple(prefix),
        maximum_layer=Layer.ABILITY,_enforce_static_component_applicability=False)
    return program_key in static_component_keys(effective.get('ability_fragments',()),
        source_designations=current_source_designations(host.state,source))


def ordered_static_source_candidates(host,present,participants):
    """Bind CR 613.8 source-removal dependencies in one shared order."""
    from dataclasses import replace
    from .continuous_effects import order_continuous_effects
    groups={}
    for effect in present:
        producer=participants.get(effect.effect_id)
        if producer is not None:groups.setdefault(producer,[]).append(effect)
    dependencies={}
    earlier=tuple(effect for effect in present if effect.layer<Layer.ABILITY)
    removals=tuple(effect for effect in present if effect.layer==Layer.ABILITY
        and any(op.op in {'remove_all_abilities','remove_ability_fragment','set_basic_land_types'}
                for op in effect.operations))
    for (object_id,program_key),effects in groups.items():
        if min(effect.layer for effect in effects)!=Layer.ABILITY:continue
        source=host.state.cards.get(object_id)
        if source is None or source.zone!='battlefield':continue
        own={effect.effect_id for effect in effects}
        base=host._compiled_base_characteristics(source,host.card_record(source),error_type=GameRuleError)
        for removal in removals:
            if removal.effect_id in own:continue
            effective=host._apply_layered_characteristic_annotations(source,base,runtime_effects=(*earlier,removal),
                maximum_layer=Layer.ABILITY,_enforce_static_component_applicability=False)
            if program_key in static_component_keys(effective.get('ability_fragments',()),
                    source_designations=current_source_designations(host.state,source)):continue
            for effect in effects:
                if effect.layer==Layer.ABILITY:
                    dependencies.setdefault(effect.effect_id,[]).append(removal.effect_id)
    bound=tuple(replace(effect,depends_on=tuple(dict.fromkeys((*effect.depends_on,*dependencies[effect.effect_id]))))
        if effect.effect_id in dependencies else effect for effect in present)
    ordered,_cycles=order_continuous_effects(bound)
    return ordered
