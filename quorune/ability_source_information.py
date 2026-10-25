from __future__ import annotations

"""Additive execution version for complete stack-ability source information."""

from collections.abc import Mapping

ABILITY_SOURCE_INFORMATION_CAPABILITY = 'target.ability_source.current_or_lki'
ABILITY_SOURCE_FILTERS = frozenset({'activated_artifact_source','noncreature_ability_source'})


def ability_source_information_version(value):
    if value is not None and (type(value) is not int or value != 1):
        raise ValueError('Unsupported ability-source information version')
    return value


def complete_ability_source_information(state):
    return ability_source_information_version(getattr(state,'ability_source_information_version',None)) is not None


def ability_source_filter_dependencies(value):
    if (not isinstance(value,Mapping) or not isinstance(value.get('predicate'),str)
            or value['predicate'] not in ABILITY_SOURCE_FILTERS):
        return ()
    if (set(value)!={'zones','categories','source_exclusion','predicate','count'}
            or tuple(value['zones'])!=('stack',) or tuple(value['categories'])!=('ability',)
            or value['source_exclusion'] is not True or type(value['count']) is not int or value['count']!=1):
        raise ValueError('Ability-source counter filters require their closed stack target schema')
    return (ABILITY_SOURCE_INFORMATION_CAPABILITY,)
