"""Versioned fixed life-gain recipient scopes and independent trust minima."""
from collections.abc import Mapping

PUBLIC_LIFE_GAIN_FIELD='public_life_gain_version'
PUBLIC_LIFE_GAIN_CAPABILITY='life.gain.fixed_public_scope'
SELECTED_PUBLIC_LIFE_GAIN_VERSION = 2
PUBLIC_LIFE_GAIN_MECHANIC='fixed-public-life-gain'
_RUNTIME_FIELDS=frozenset({'reason','source','cause','_runtime_source','_replacement_selections'})


def validate_public_life_gain(effect, *, runtime=False):
    allowed={'op',PUBLIC_LIFE_GAIN_FIELD,'scope','delta'}
    if runtime:allowed|=_RUNTIME_FIELDS
    if (not isinstance(effect,Mapping) or set(effect)-allowed
            or not {'op',PUBLIC_LIFE_GAIN_FIELD,'scope','delta'}<=set(effect)
            or effect.get('op')!='life' or type(effect.get(PUBLIC_LIFE_GAIN_FIELD)) is not int
            or effect[PUBLIC_LIFE_GAIN_FIELD]!=1 or type(effect.get('delta')) is not int
            or effect['delta']<1 or effect.get('scope') not in ('all_active_players','controller_opponents')):
        raise ValueError('Public life gain requires a complete version-one fixed recipient scope')
    return (PUBLIC_LIFE_GAIN_CAPABILITY,'life.change.effect')


def validate_selected_public_life_gain(effect):
    """Version two is a materialized selected public gain with a resolving source."""
    required = {"op", PUBLIC_LIFE_GAIN_FIELD, "players", "delta", "_runtime_source"}
    if (not required <= set(effect) or set(effect) - required - _RUNTIME_FIELDS
            or effect.get("op") != "life" or type(effect[PUBLIC_LIFE_GAIN_FIELD]) is not int
            or effect[PUBLIC_LIFE_GAIN_FIELD] != SELECTED_PUBLIC_LIFE_GAIN_VERSION):
        raise ValueError("Selected public life gain requires a complete version-two scope")


def public_life_gain_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (len(effects)!=1 or target_schema is not None
            or not {'cr-119-life',PUBLIC_LIFE_GAIN_MECHANIC}<=set(mechanic_ids)):return ()
    try:return validate_public_life_gain(effects[0])
    except (TypeError,ValueError):return ()


def public_life_gain_runtime_dependencies(program):
    required=set()
    def visit(value):
        if isinstance(value,Mapping):
            if PUBLIC_LIFE_GAIN_FIELD in value or value.get('op')=='life' and 'scope' in value:
                required.update(validate_public_life_gain(value))
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    visit(program.effects)
    return tuple(sorted(required))
