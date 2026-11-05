"""Descriptor-owned capability requirements for qualified destruction sets."""
QUALIFIED_DESTRUCTION_CAPABILITY='permanent.destroy.qualified_fixed_set'


def qualified_destruction_capabilities(query):
    from ..object_predicate import object_query_capabilities
    advanced=(query.numeric_characteristic is not None or query.commander is not None
        or bool(query.colors_none or query.excluded_supertypes or query.any_of)
        or 'aura' in query.excluded_subtypes
        or bool(set(query.subtypes_all)&{'aura','equipment'}))
    return tuple(sorted({*((QUALIFIED_DESTRUCTION_CAPABILITY,) if advanced else ()),
        *(('state_query.commander.designation',) if query.commander is not None else ()),
        *object_query_capabilities(query)}))


def qualified_destruction_payload_dependencies(value):
    if value.get('op')!='destroy_all':return ()
    from ..affected_permanents import AffectedPermanentSetSpec
    return qualified_destruction_capabilities(AffectedPermanentSetSpec.from_dict(value['set']).query)
