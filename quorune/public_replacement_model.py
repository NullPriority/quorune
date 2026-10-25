from __future__ import annotations

"""One unchanged public target with independently owned fixed branches."""

from collections.abc import Mapping


def _target_references(value):
    if isinstance(value,str):return {value} if value.startswith('$target') else set()
    if isinstance(value,Mapping):return {ref for child in value.values() for ref in _target_references(child)}
    if isinstance(value,(list,tuple)):return {ref for child in value for ref in _target_references(child)}
    return set()


def public_target_binding_closed(schema):
    return (isinstance(schema,Mapping) and type(schema.get('count')) is int and schema['count']==1
        and isinstance(schema.get('zones'),(list,tuple)) and bool(schema['zones'])
        and all(type(zone) is str and zone in {'battlefield','stack','player','graveyard'} for zone in schema['zones']))


def public_replacement_target_closed(effects, schema):
    if not public_target_binding_closed(schema) or not 1<=len(effects)<=3:return False
    refs=_target_references(effects)
    if '$target.0' not in refs or not refs <= {'$target.0','$target.current_controller.0'}:return False
    for effect in effects:
        if '$target.0' not in _target_references(effect):return False
        if 'source' in effect and effect['source']!='$source':return False
        if '$target.current_controller.0' in _target_references(effect) and not (
                effect.get('op')=='counter_unless_pay' and effect.get('stack')=='$target.0'
                and effect.get('player')=='$target.current_controller.0'):return False
    return True
