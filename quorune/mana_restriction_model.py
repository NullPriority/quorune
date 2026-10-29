from __future__ import annotations

"""Immutable canonical spending-predicate encoding, independent of game state."""

_RESTRICTION_PREFIX = "mana-restriction-v1"
_CLAUSE_AXES = frozenset(
    {"any", "types_any", "types_none", "subtypes_any", "supertypes_any"}
)
_LEGACY_RESTRICTIONS = frozenset(
    {
        "artifact_spell_or_ability",
        "artifact_spell_only",
        "creature_spell_only",
        "legendary_spell_uncounterable",
        "nonartifact_spell_prohibited",
    }
)

def _token(value: str) -> str:
    return "_".join(value.casefold().split())


def canonical_mana_spend_restriction(
    clauses: tuple[tuple[str, str, tuple[str, ...]], ...],
) -> str:
    """Return one canonical disjunction of closed spend-context predicates."""

    normalized: list[str] = []
    for kind, axis, raw_values in clauses:
        if kind not in {"spell", "ability"} or axis not in _CLAUSE_AXES:
            raise ValueError("mana spend restriction clause is unsupported")
        values = tuple(sorted({_token(value) for value in raw_values if value}))
        if axis == "any":
            if values not in {(), ("*",)}:
                raise ValueError("unrestricted ability clause takes no values")
            values = ("*",)
        elif not values:
            raise ValueError("mana spend restriction clause requires values")
        normalized.append(f"{kind}.{axis}={','.join(values)}")
    if not normalized or len(normalized) != len(set(normalized)):
        raise ValueError("mana spend restriction clauses must be nonempty and unique")
    return _RESTRICTION_PREFIX + "|" + "|".join(sorted(normalized))


def _typed_restriction(
    value: str,
) -> tuple[tuple[str, str, frozenset[str]], ...] | None:
    if not value.startswith(_RESTRICTION_PREFIX + "|"):
        return None
    clauses: list[tuple[str, str, frozenset[str]]] = []
    for raw in value.split("|")[1:]:
        left, separator, right = raw.partition("=")
        kind, dot, axis = left.partition(".")
        values = frozenset(part for part in right.split(",") if part)
        if (
            separator != "="
            or dot != "."
            or kind not in {"spell", "ability"}
            or axis not in _CLAUSE_AXES
            or (axis == "any" and values != {"*"})
            or (axis != "any" and not values)
        ):
            return None
        clauses.append((kind, axis, values))
    try:
        canonical = canonical_mana_spend_restriction(
            tuple((kind, axis, tuple(values)) for kind, axis, values in clauses)
        )
    except ValueError:
        return None
    return tuple(clauses) if canonical == value else None


def valid_mana_spend_restriction(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and (value in _LEGACY_RESTRICTIONS or _typed_restriction(value) is not None)
    )
