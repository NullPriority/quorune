from __future__ import annotations

"""Explicit live permanent mana-value bounds; graveyard wire shapes stay separate."""

PERMANENT_MANA_VALUE_QUERY_CAPABILITY = 'state_query.permanent.mana_value_bound'
EVEN_MANA_VALUE_BLOCKER_CAPABILITY = "combat.declaration.opponent_even_mana_value_blockers"


def is_even_mana_value(value):
    return type(value) in {int, float} and value >= 0 and value % 2 == 0


def permanent_mana_value_matches(row, query):
    return query.permanent_mana_value_max is None or row.mana_value <= query.permanent_mana_value_max


def validate_permanent_mana_value_query(query):
    from .object_predicate import ObjectQueryError

    if (type(query.permanent_mana_value_max) is not int
            or not 0 <= query.permanent_mana_value_max <= 20
            or query.zones != ('battlefield',) or 'creature' not in query.types_all
            or query.mana_value_max is not None or query.any_of
            or query.numeric_characteristic is not None or query.known_to_actor is not None):
        raise ObjectQueryError('Permanent mana-value bounds require public battlefield creatures and a bound from zero through twenty')

def even_mana_value_blocker_restriction():
    from .declaration_fragments import DeclarationObjectPredicate, DeclarationRestrictionTemplate

    return DeclarationRestrictionTemplate(
        template_id="opponents-even-mana-value-blockers-v1", declarations=("block",),
        scope="source_opponents", subject=DeclarationObjectPredicate(types_any=("creature",), mana_value_even=True),
    )


def even_mana_value_blocker_dependencies(template):
    if not any(getattr(getattr(template, name, None), "mana_value_even", False)
               for name in ("subject", "opposing", "matching")):
        return ()
    if template != even_mana_value_blocker_restriction():
        raise ValueError("An even mana-value predicate requires its original opponent blocking restriction")
    return (EVEN_MANA_VALUE_BLOCKER_CAPABILITY,)
