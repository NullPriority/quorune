from __future__ import annotations

"""Typed production and spending contexts for restricted mana units."""


_CONTEXT_PREFIX = "mana-spend-v1"
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
_LEGACY_RESTRICTION_TAILS = {
    (
        "spend this mana only to",
        "cast artifact spells or activate abilities of artifacts",
    ): "artifact_spell_or_ability",
    (
        "spend this mana only to",
        "cast an artifact spell",
    ): "artifact_spell_only",
    (
        "spend this mana only to",
        "cast a creature spell",
    ): "creature_spell_only",
    (
        "spend this mana only to",
        "cast a legendary spell, and that spell can't be countered",
    ): "legendary_spell_uncounterable",
    (
        "spend this mana only to",
        "cast a legendary spell and that spell can't be countered",
    ): "legendary_spell_uncounterable",
    (
        "this mana can't be spent to",
        "cast nonartifact spells",
    ): "nonartifact_spell_prohibited",
    (
        "this mana can't be spent to",
        "cast a nonartifact spell",
    ): "nonartifact_spell_prohibited",
}


def _token(value: str) -> str:
    return "_".join(value.casefold().split())


def _typed_context(kind: str, type_line: str) -> str:
    from .characteristic_evaluation import type_parts

    if kind not in {"spell", "ability"}:
        raise ValueError("mana spend context kind is unsupported")
    types, subtypes, supertypes = type_parts(type_line)
    fields = {
        "kind": kind,
        "types": ",".join(sorted(_token(value) for value in types)),
        "subtypes": ",".join(sorted(_token(value) for value in subtypes)),
        "supertypes": ",".join(sorted(_token(value) for value in supertypes)),
    }
    return _CONTEXT_PREFIX + ";" + ";".join(
        f"{field}={fields[field]}"
        for field in ("kind", "types", "subtypes", "supertypes")
    )


def spell_mana_spend_context(type_line: str) -> str:
    """Encode public spell characteristics consumed by restriction predicates."""

    return _typed_context("spell", type_line)


def ability_mana_spend_context(type_line: str) -> str:
    """Encode public ability-source characteristics for mana payment."""

    return _typed_context("ability", type_line)


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


def legacy_mana_spend_restriction_for_tail(
    marker: str,
    tail: str,
) -> str | None:
    """Recognize only one completely consumed historical restriction tail."""

    normalized = (
        " ".join(marker.casefold().split()),
        " ".join(tail.casefold().split()).rstrip("."),
    )
    return _LEGACY_RESTRICTION_TAILS.get(normalized)


def _context_facts(
    spend_context: str | None,
) -> tuple[str | None, frozenset[str], frozenset[str], frozenset[str]]:
    if not spend_context:
        return None, frozenset(), frozenset(), frozenset()
    if spend_context.startswith(_CONTEXT_PREFIX + ";"):
        rows = spend_context.split(";")
        pairs = tuple(row.split("=", 1) for row in rows[1:] if "=" in row)
        fields = dict(pairs)
        if (
            len(pairs) != 4
            or set(fields) != {"kind", "types", "subtypes", "supertypes"}
            or fields["kind"] not in {"spell", "ability"}
        ):
            return None, frozenset(), frozenset(), frozenset()
        values = tuple(
            frozenset(value for value in fields[field].split(",") if value)
            for field in ("types", "subtypes", "supertypes")
        )
        return fields["kind"], *values
    if spend_context in {"ability", "artifact_ability"}:
        return (
            "ability",
            frozenset({"artifact"} if spend_context == "artifact_ability" else ()),
            frozenset(),
            frozenset(),
        )
    if "spell" in spend_context:
        tokens = frozenset(spend_context.split("_"))
        return (
            "spell",
            frozenset(
                value
                for value in ("artifact", "creature")
                if value in tokens
            ),
            frozenset(),
            frozenset({"legendary"} if "legendary" in tokens else ()),
        )
    return None, frozenset(), frozenset(), frozenset()


def mana_restriction_allows(
    restriction: str,
    spend_context: str | None,
) -> bool:
    """Return whether one typed restricted unit may pay in this context."""

    kind, types, subtypes, supertypes = _context_facts(spend_context)
    is_spell = kind == "spell"
    is_artifact = "artifact" in types
    is_creature = "creature" in types
    is_legendary = "legendary" in supertypes
    if restriction == "artifact_spell_or_ability":
        return (
            (is_spell and is_artifact)
            or (kind == "ability" and is_artifact)
        )
    if restriction == "artifact_spell_only":
        return is_spell and is_artifact
    if restriction == "creature_spell_only":
        return is_spell and is_creature
    if restriction == "nonartifact_spell_prohibited":
        return not (is_spell and not is_artifact)
    if restriction == "legendary_spell_uncounterable":
        return is_spell and is_legendary
    clauses = _typed_restriction(restriction)
    if clauses is None:
        return False
    for clause_kind, axis, values in clauses:
        if clause_kind != kind:
            continue
        if axis == "any":
            return True
        observed = {
            "types_any": types,
            "types_none": types,
            "subtypes_any": subtypes,
            "supertypes_any": supertypes,
        }[axis]
        if axis == "types_none":
            if not observed.intersection(values):
                return True
        elif observed.intersection(values):
            return True
    return False


__all__ = [
    "ability_mana_spend_context",
    "canonical_mana_spend_restriction",
    "legacy_mana_spend_restriction_for_tail",
    "mana_restriction_allows",
    "spell_mana_spend_context",
    "valid_mana_spend_restriction",
]
