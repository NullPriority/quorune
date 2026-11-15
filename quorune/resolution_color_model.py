"""Versioned fixed-color operands; historical version fourteen stays intact."""
from collections.abc import Mapping

from .resolution_characteristic_model import CHARACTERISTIC_OPERATION, decode_fixed_target_color
from .target_cardinality import validate_selected_target_count

SCOPED_TARGET_COLOR_VERSION=22
TARGET_COLOR_SET_VERSION=23
TARGET_COLOR_VERSIONS=frozenset({SCOPED_TARGET_COLOR_VERSION,TARGET_COLOR_SET_VERSION})
TARGET_COLOR_SCOPE_MECHANIC='fixed-target-color-scope'
TARGET_COLOR_SCOPE_CAPABILITY='zone.object.fixed_resolution_color_scope'


def decode_resolution_color(effect,*,materialized=False):
    if not isinstance(effect,Mapping):raise ValueError('Color instruction requires a mapping')
    version=effect.get('schema_version');grouped=version==TARGET_COLOR_SET_VERSION
    fields={'op','schema_version','colors','color_mode','duration'}
    fields|={'cards','maximum_targets'} if grouped else {'card'}
    if grouped and 'announced_target_count' in effect:fields.add('announced_target_count')
    allowed=fields|({'_runtime_source','reason'} if materialized else set())
    if (type(version) is not int or version not in TARGET_COLOR_VERSIONS or not fields<=set(effect)<=allowed):
        raise ValueError('Color instruction version or fields are unsupported')
    mode=effect['color_mode']
    if mode not in {'set','add'} or mode=='add' and not effect['colors']:
        raise ValueError('Color operation must set colors or add a printed color')
    common={k:v for k,v in effect.items() if k in {'op','colors','duration'}}
    common.update(schema_version=14,card='color-recipient' if materialized else '$target.0')
    _,colors,duration=decode_fixed_target_color(common,materialized=materialized)
    if grouped:
        maximum=effect['maximum_targets']
        if 'announced_target_count' in effect:
            if effect['announced_target_count'] is not True:raise ValueError('Selected color target count must be true')
            if not materialized:validate_selected_target_count(maximum)
            elif type(maximum) is not int or maximum<1:raise ValueError('Selected color bound must be positive')
        elif type(maximum) is not int or not 1<=maximum<=6:
            raise ValueError('Color set requires a fixed bounded count')
        cards=effect['cards']
        if not materialized:
            if cards!='$targets':raise ValueError('Color set requires its declared target set')
            refs=('$targets',)
        else:
            if (not isinstance(cards,(list,tuple)) or any(type(r) is not str or not r or r.startswith('$') for r in cards)
                    or len(cards)!=len(set(cards)) or len(cards)>maximum):raise ValueError('Resolved color targets are malformed')
            refs=tuple(cards)
    else:
        reference=effect['card']
        if type(reference) is not str or not reference or (reference.startswith('$') if materialized else reference!='$target.0'):
            raise ValueError('Color instruction requires one declared target')
        refs=(reference,)
    return refs,colors,duration,mode
