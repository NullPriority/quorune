"""Independent capability closure for complete life-total setting effects."""
from ..life_total_setting_model import LifeTotalValueSpec

LIFE_TOTAL_SETTING_CAPABILITY='life.change.set_total'


def life_total_setting_capabilities(*,effects,target_schema,mechanic_ids):
    if len(effects)!=1 or 'cr-119-life' not in mechanic_ids:return ()
    effect=effects[0]
    base={'op','schema_version','value'}
    if (effect.get('op')!='set_life_total' or type(effect.get('schema_version')) is not int
            or effect['schema_version']!=1):return ()
    try:LifeTotalValueSpec.from_dict(effect['value'])
    except (ValueError,TypeError,KeyError):return ()
    if set(effect)==base|{'scope'}:
        if effect['scope']!='all_active_players' or target_schema is not None or 'cr-101-the-magic-golden-rules' not in mechanic_ids:return ()
    elif set(effect)==base|{'player'}:
        if effect['player']=='$controller':
            if target_schema is not None:return ()
        elif effect['player']=='$target.0':
            if (dict(target_schema or {}) not in (
                    {'zones':['player'],'categories':['player'],'player_relation':'any','count':1},
                    {'zones':['player'],'categories':['player'],'player_relation':'opponent','count':1})
                    or 'cr-115-targets' not in mechanic_ids):return ()
        else:return ()
    else:return ()
    return tuple(sorted({LIFE_TOTAL_SETTING_CAPABILITY,'life.change.effect',
        *({'target.revalidate_resolution'} if target_schema is not None else set())}))
