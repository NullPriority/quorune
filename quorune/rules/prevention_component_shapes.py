from __future__ import annotations

"""Closed fixed all-damage shield as one ordered resolution component."""

from collections.abc import Mapping
from ..damage_modifier_state import DamagePreventionScope
from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed


def fixed_prevention_component_capabilities(*,effects,target_schema,mechanic_ids):
    if 'cr-615-prevention-effects' not in set(mechanic_ids) or len(effects)!=1:return ()
    effect=effects[0]
    from ..attached_prevention_model import attached_prevention_dependencies
    attached=effect.get('shield') if isinstance(effect,Mapping) and effect.get('op')=='choose_damage_source' else effect
    try:attachment_dependencies=attached_prevention_dependencies(attached)
    except (TypeError,ValueError):return ()
    if attachment_dependencies:
        if target_schema is not None:return ()
        if effect is not attached:
            if set(effect)!={'op','prompt','source_predicate','shield'}:return ()
            from ..object_query import ObjectQuerySpec,validate_chosen_damage_source_predicate
            try:validate_chosen_damage_source_predicate(ObjectQuerySpec.from_dict(effect['source_predicate']))
            except (TypeError,ValueError):return ()
        return ('damage.prevention.persistent_amount',*attachment_dependencies)
    from ..prevention_recipient_sets import prevention_recipient_set_dependencies
    if (isinstance(effect, Mapping) and set(effect)=={'op','source','subject','mode','duration','amount','selector'}
            and effect.get('source')=='$source' and effect.get('subject')=='*'
            and effect.get('mode')=='amount' and effect.get('duration')=='until_end_of_turn'
            and type(effect.get('amount')) is int and effect['amount']>0 and target_schema is None):
        try:own=prevention_recipient_set_dependencies(effect)
        except (ValueError,TypeError):return ()
        if own:return ('damage.prevention.persistent_amount',*own)
    if (not isinstance(effect,Mapping) or set(effect)!={'op','source','subject','mode','duration','damage_kind','scope'}
            or effect.get('op')!='create_damage_prevention_shield' or effect.get('source')!='$source'
            or effect.get('subject')!='*' or effect.get('mode')!='all'
            or effect.get('duration')!='until_end_of_turn' or effect.get('damage_kind') not in {'any','combat','noncombat'}):return ()
    try:scope=DamagePreventionScope.from_dict(effect['scope'])
    except (ValueError,TypeError,KeyError):return ()
    references=(scope.source_ref,scope.target_ref,scope.excluded_source_ref,scope.excluded_target_ref)
    if any(value not in {None,'$source','$target.0'} for value in references):return ()
    targeted='$target.0' in references
    if targeted != (target_schema is not None):return ()
    if targeted and not direct_permanent_target_schema_is_closed(target_schema):return ()
    return ('damage.prevention.persistent_amount','target.revalidate_resolution') if targeted else ('damage.prevention.persistent_amount',)
