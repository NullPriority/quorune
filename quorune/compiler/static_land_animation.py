from __future__ import annotations

"""Fixed live land-creature settings that explicitly retain land types."""

import re
from ..object_predicate import ObjectQuerySpec
from .continuous_templates import _attached_modifier


def static_land_animation_handler(text):
    match=re.fullmatch(r'All (?P<subject>lands|Plains|Islands|Swamps|Mountains|Forests) are (?P<power>\d+)/(?P<toughness>\d+) '
        r'(?P<color>white |blue |black |red |green |colorless )?creatures that are still lands\.',text,re.I)
    if match is None:return None
    subject=match['subject'].casefold();types={'plains':'plains','islands':'island','swamps':'swamp','mountains':'mountain','forests':'forest'}
    predicate=ObjectQuerySpec(zones=('battlefield',),types_all=('land',),subtypes_all=(types[subject],) if subject in types else ())
    modifier=_attached_modifier();modifier['type_operations']=[{'op':'add_types','field':'card_types','values':['Creature']}]
    modifier['base_power']=int(match['power']);modifier['base_toughness']=int(match['toughness'])
    color=(match['color'] or '').strip().casefold()
    if color:
        modifier['color_operations']=[{'op':'remove_all_colors'}] if color=='colorless' else [{'op':'set_colors','values':[{'white':'W','blue':'U','black':'B','red':'R','green':'G'}[color]]}]
    descriptor={'handler_id':'continuous.characteristics.fixed-public-setting.v1','schema_version':1,'event':'characteristics.evaluate',
        'target':{'kind':'fixed_query','controller':'any','predicate':predicate.to_dict(),'exclude_source':False},'modifier':modifier}
    from ..semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
    FixedCharacteristicSettingsHandler().validate(descriptor)
    return 'static-fixed-land-animation-v1',descriptor,('continuous.characteristics.fixed_public_setting',)
