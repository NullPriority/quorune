from __future__ import annotations

"""Additive execution version for complete stack-ability source information."""

from collections.abc import Mapping

ABILITY_SOURCE_INFORMATION_VERSION = 2
ABILITY_SOURCE_INFORMATION_CAPABILITY = 'target.ability_source.current_or_lki'
QUALIFIED_COPY_SOURCE_FILTERS = frozenset({'artifact_ability_source', 'colorless_ability_source'})
ACTIVATED_PUBLIC_PERMANENT_SOURCE_FILTER = "activated_artifact_creature_enchantment_land_source"
ACTIVATED_PUBLIC_PERMANENT_SOURCE_TYPES = frozenset({"artifact", "creature", "enchantment", "land"})
ABILITY_SOURCE_FILTERS = frozenset({
    "activated_artifact_source", "noncreature_ability_source", ACTIVATED_PUBLIC_PERMANENT_SOURCE_FILTER,
    *QUALIFIED_COPY_SOURCE_FILTERS,
})


def ability_source_information_version(value):
    if value is not None and (type(value) is not int or value not in {1, ABILITY_SOURCE_INFORMATION_VERSION}):
        raise ValueError('Unsupported ability-source information version')
    return value


def complete_ability_source_information(state):
    return ability_source_information_version(getattr(state,'ability_source_information_version',None)) is not None


def retains_original_source_tap_status(state):
    version = ability_source_information_version(getattr(state, "ability_source_information_version", None))
    return version == ABILITY_SOURCE_INFORMATION_VERSION


def ability_source_filter_dependencies(value):
    if (not isinstance(value,Mapping) or not isinstance(value.get('predicate'),str)
            or value['predicate'] not in ABILITY_SOURCE_FILTERS):
        return ()
    fields = {'zones','categories','source_exclusion','predicate','count'}
    if value['predicate'] in QUALIFIED_COPY_SOURCE_FILTERS:
        fields.add('controller_relation')
        if value.get('controller_relation') != 'you':
            raise ValueError('Qualified ability-copy sources require controlled abilities')
    if (set(value)!=fields
            or tuple(value['zones'])!=('stack',) or tuple(value['categories'])!=('ability',)
            or value['source_exclusion'] is not True or type(value['count']) is not int or value['count']!=1):
        raise ValueError('Ability-source counter filters require their closed stack target schema')
    return (ABILITY_SOURCE_INFORMATION_CAPABILITY,)
