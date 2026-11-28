"""Exact battlefield receiver queries qualified by the ability they grant."""

from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec, object_query_capabilities


def ability_qualified_query_subject(subject, abilities):
    subject = subject.casefold()
    abilities = abilities.casefold()
    if subject == "permanents you control with counters on them" and abilities == "ward {1}":
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            state_predicate=PermanentStatePredicateSpec(minimum_total_counter_count=1),
        )
        return (
            ("source_controller", predicate, False),
            object_query_capabilities(predicate),
            "continuous-controlled-countered-permanent-ward-v1",
        )
    if subject == "creatures you control with toxic" and abilities == "lifelink":
        predicate = ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",), keywords_all=("toxic",),
        )
        return (
            ("source_controller", predicate, False),
            ("damage.result.toxic",),
            "continuous-fixed-query-keyword-grant-v2",
        )
    return None
