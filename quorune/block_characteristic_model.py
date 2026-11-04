"""CR509.3: immutable public qualities of one actual blocking counterpart."""
from collections.abc import Mapping
from .object_predicate import ObjectQuerySpec

BLOCK_CHARACTERISTIC_CAPABILITY='trigger.event.block_counterpart_characteristics'
BLOCK_CHARACTERISTIC_CONDITION='block_counterpart_matches'
BLOCK_CHARACTERISTIC_CONTEXT='block_counterpart_characteristics'
BLOCK_FACT_COLLECTIONS=('types','subtypes','supertypes','colors','keywords')


def block_characteristics_version(value):
    if value is not None and (type(value)is not int or value!=1):raise ValueError('Unsupported block characteristic facts version')
    return value


def block_counterpart_query(value):
    query=ObjectQuerySpec.from_dict(value)
    allowed=ObjectQuerySpec(zones=('battlefield',),types_all=query.types_all,types_any=query.types_any,
        excluded_types=query.excluded_types,subtypes_all=query.subtypes_all,subtypes_any=query.subtypes_any,
        excluded_subtypes=query.excluded_subtypes,supertypes_all=query.supertypes_all,excluded_supertypes=query.excluded_supertypes,
        colors_all=query.colors_all,colors_any=query.colors_any,colors_none=query.colors_none,colorless=query.colorless,
        keywords_all=query.keywords_all,keywords_none=query.keywords_none,numeric_characteristic=query.numeric_characteristic)
    if query!=allowed or 'creature'not in query.types_all:
        raise ValueError('Block counterpart queries require only represented sealed creature characteristics')
    if query.numeric_characteristic is not None and query.numeric_characteristic.characteristic.value not in {'power','toughness'}:
        raise ValueError('Block counterpart numeric query requires power or toughness')
    return query


def validate_block_characteristics(value):
    fields={'schema_version','object_id','reference','logical_object_id','owner','controller','power','toughness',*BLOCK_FACT_COLLECTIONS}
    if (not isinstance(value,Mapping) or set(value)!=fields or type(value['schema_version'])is not int or value['schema_version']!=1
            or any(type(value[k])is not str or not value[k] for k in ('object_id','reference','logical_object_id','owner','controller'))):
        raise ValueError('Sealed block characteristics have a closed version and identity')
    for name in BLOCK_FACT_COLLECTIONS:
        values=value[name]
        if not isinstance(values,(list,tuple)) or any(type(v)is not str or not v for v in values) or len(values)!=len(set(values)):
            raise ValueError('Sealed block characteristics require distinct public term sets')
    if not set(value['colors'])<={'W','U','B','R','G'} or any(term!=term.casefold() for name in ('types','subtypes','supertypes','keywords') for term in value[name]):
        raise ValueError('Sealed block characteristic terms must use canonical public values')
    if 'creature'not in value['types'] or any(value[k]is not None and type(value[k])is not int for k in ('power','toughness')):
        raise ValueError('Sealed blocking participants require creature type and bounded numeric facts')
    return value
