"""Resolve placement once, then reuse the ordinary search movement continuation."""
from dataclasses import replace
from .conditional_search_model import ConditionalSearchSpec
from .continuous_conditions import FixedPublicStateConditionSpec
from .resolution_conditions import ResolutionConditionBinding


def materialize_conditional_search(host,context,values):
    if context.effect.get('schema_version')!=4:return context
    spec=ConditionalSearchSpec.from_effect(context.effect);p=spec.placement;matched=False
    if values:
        card=host._resolve_object(context.seat,values[0],zones={'library'},owned_only=True)
        if p['kind']=='public_state':
            matched=host._fixed_public_state_condition_holds(ResolutionConditionBinding(controller=context.seat),
                FixedPublicStateConditionSpec.from_dict(p['condition']),require_available=True)
        else:
            data=host._effective_card_data(card)
            if p['kind']=='mana_value_at_most':
                from .public_quantity_reductions import public_mana_value
                matched=public_mana_value(data)<=p['maximum']
            else:matched=p['card_type'] in host._type_parts(str(data.get('type_line') or ''))[0]
    search=replace(spec.search,destination='battlefield' if matched else 'hand',enters_tapped=p['enters_tapped'] if matched else False)
    return replace(context,effect=search.effect())
