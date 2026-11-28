from __future__ import annotations

"""Typed production and spending contexts for restricted mana units."""


import json

from .mana_restriction_model import (
    _token, _typed_restriction, canonical_mana_spend_restriction, valid_mana_spend_restriction,
    ROOM_CAST_UNLOCK_MANA_RESTRICTION, ROOM_UNLOCK_MANA_SPEND_CONTEXT, ROOM_UNLOCK_SPEND_KIND,
)
from .mana_spend_rules import decode_mana_spend_rule
from .mana_spending_model import ManaSpendingMode

_CONTEXT_PREFIX = "mana-spend-v1"
_BATTLEFIELD_CREATURE_ABILITY_CONTEXT_PREFIX = "mana-spend-battlefield-creature-ability-v1"
_EXILED_SPELL_ANY_TYPE_PREFIX = "mana-spend-exiled-spell-any-type-v1:"
_EXILE_PLAY_CASTER_FIELD = ";exile-play-caster="
_EXILE_PLAY_COLOR_CASTER_FIELD = ";exile-play-color-caster="
_SPELL_ORIGIN_FIELD = "cast-origin"
_EXILE_ORIGIN_TAIL = ";" + _SPELL_ORIGIN_FIELD + "=exile"
_LEGACY_RESTRICTION_TAILS = {
    ("spend this mana only to", "cast room spells and unlock doors"): ROOM_CAST_UNLOCK_MANA_RESTRICTION,
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


def _typed_context(
    kind: str, type_line: str, *, source_zone: str | None = None, source_type_line: str | None = None,
) -> str:
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
    source_types = types if source_type_line is None else type_parts(source_type_line)[0]
    prefix = (
        _BATTLEFIELD_CREATURE_ABILITY_CONTEXT_PREFIX
        if kind == "ability" and source_zone == "battlefield" and "creature" in source_types
        else _CONTEXT_PREFIX
    )
    return prefix + ";" + ";".join(
        f"{field}={fields[field]}"
        for field in ("kind", "types", "subtypes", "supertypes")
    )


def spell_mana_spend_context(type_line: str) -> str:
    """Encode public spell characteristics consumed by restriction predicates."""

    return _typed_context("spell", type_line)


def with_exile_cast_origin(spend_context: str) -> str:
    if not isinstance(spend_context, str) or not spend_context.startswith(_CONTEXT_PREFIX + ";kind=spell;"):
        raise ValueError("Exile casting requires its ordinary spell context")
    context = spend_context + _EXILE_ORIGIN_TAIL
    if not _context_facts(context)[-1]:
        raise ValueError("Exile origin must be the only new spell context fact")
    return context


def bind_exiled_spell_mana_spending(spend_context: str, *, payer: str) -> str:
    """Carry a validated cast permission through source announcement and payment."""
    if (type(payer) is not str or not payer or type(spend_context) is not str
            or not spend_context.startswith(_CONTEXT_PREFIX + ";kind=spell;")):
        raise ValueError("Exile mana permission requires a spell context and its payer")
    return _EXILED_SPELL_ANY_TYPE_PREFIX + json.dumps([payer, spend_context], separators=(",", ":"))


def _read_exiled_spell_spending(spend_context: str | None) -> tuple[str, str] | None:
    if not isinstance(spend_context, str) or not spend_context.startswith(_EXILED_SPELL_ANY_TYPE_PREFIX):
        return None
    try:
        values = json.loads(spend_context[len(_EXILED_SPELL_ANY_TYPE_PREFIX):])
    except ValueError:
        return None
    if (not isinstance(values, list) or len(values) != 2 or type(values[0]) is not str or not values[0]
            or type(values[1]) is not str or not values[1].startswith(_CONTEXT_PREFIX + ";kind=spell;")):
        return None
    return values[0], values[1]


def exiled_spell_any_type_payer(spend_context: str | None) -> str | None:
    parts = _read_exiled_spell_spending(spend_context)
    return parts[0] if parts and _context_facts(parts[1])[0] == "spell" else None


def exile_play_mana_spend_context(spend_context: str, caster: str, *, color_only: bool = False) -> str:
    """Scope one color or type payment policy to the caster holding the grant."""
    if not caster or not spend_context.startswith(_CONTEXT_PREFIX + ";kind=spell;"):
        raise ValueError("Exile play mana permission requires its original caster and spell context")
    field = _EXILE_PLAY_COLOR_CASTER_FIELD if color_only else _EXILE_PLAY_CASTER_FIELD
    return spend_context + field + caster.encode("utf-8").hex()


def _split_exile_play_caster(spend_context: str | None) -> tuple[str | None, str | None, ManaSpendingMode | None]:
    if not spend_context:
        return spend_context, None, None
    color_only = _EXILE_PLAY_COLOR_CASTER_FIELD in spend_context
    field = _EXILE_PLAY_COLOR_CASTER_FIELD if color_only else _EXILE_PLAY_CASTER_FIELD
    context, separator, caster_hex = spend_context.rpartition(field)
    if (not separator or _EXILE_PLAY_CASTER_FIELD in context or _EXILE_PLAY_COLOR_CASTER_FIELD in context
            or not context.startswith(_CONTEXT_PREFIX + ";kind=spell;")):
        return spend_context, None, None
    try:
        caster = bytes.fromhex(caster_hex).decode("utf-8")
    except ValueError:
        return spend_context, None, None
    if not caster or caster.encode("utf-8").hex() != caster_hex:
        return spend_context, None, None
    return context, caster, ManaSpendingMode.ANY_COLOR if color_only else ManaSpendingMode.ANY_TYPE


def exile_play_mana_caster(spend_context: str | None) -> str | None:
    context, caster, _mode = _split_exile_play_caster(spend_context)
    if caster is None or _context_facts(context)[0] != "spell":
        return None
    return caster


def exile_play_mana_mode(spend_context: str | None, caster: str) -> ManaSpendingMode | None:
    context, grantee, mode = _split_exile_play_caster(spend_context)
    return mode if grantee == caster and _context_facts(context)[0] == "spell" else None


def ability_mana_spend_context(
    type_line: str, *, source_zone: str | None = None, source_type_line: str | None = None,
) -> str:
    """Encode the ability's source when activation begins and the existing payment facts."""
    return _typed_context("ability", type_line, source_zone=source_zone, source_type_line=source_type_line)


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


_FIELD = ";spell-colors="
_SPELL_CONTEXT = "mana-spend-v1;kind=spell;"


def add_spell_colors(context, colors):
    if not isinstance(context, str) or not context.startswith(_SPELL_CONTEXT) or _FIELD in context:
        raise ValueError("Spell colors require one current spell payment context")
    return context + _FIELD + "".join(color for color in "WUBRG" if color in colors)


def split_spell_colors(context):
    if not isinstance(context, str) or _FIELD not in context:
        return context, ()
    rest, _, colors = context.partition(_FIELD)
    if not rest.startswith(_SPELL_CONTEXT) or colors != "".join(color for color in "WUBRG" if color in colors):
        return "", ()
    return rest, tuple(colors)


def with_spell_spend_colors(spend_context, colors):
    """Attach canonical color facts without changing other spending authorities."""
    exiled = _read_exiled_spell_spending(spend_context)
    if exiled:
        payer, original = exiled
        return bind_exiled_spell_mana_spending(add_spell_colors(original, colors), payer=payer)
    original, caster, mode = _split_exile_play_caster(spend_context)
    enriched = add_spell_colors(original, colors)
    return exile_play_mana_spend_context(enriched, caster, color_only=mode is ManaSpendingMode.ANY_COLOR) if caster else enriched


def _spell_context_colors(spend_context):
    exiled = _read_exiled_spell_spending(spend_context)
    original, _, _ = _split_exile_play_caster(exiled[1] if exiled else spend_context)
    _, colors = split_spell_colors(original)
    return colors


_ACTUAL_MANA_COLOR_FIELD = ";actual-payment-color="


def _split_actual_ability_color(context):
    if not isinstance(context, str) or _ACTUAL_MANA_COLOR_FIELD not in context:
        return context, None
    original, _, color = context.partition(_ACTUAL_MANA_COLOR_FIELD)
    if (not original.startswith((_CONTEXT_PREFIX + ";kind=ability;",
                                  _BATTLEFIELD_CREATURE_ABILITY_CONTEXT_PREFIX + ";kind=ability;"))
            or color not in tuple("WUBRG")):
        return "", None
    return original, color


def actual_color_ability_spend_context(context, color):
    enriched = context + _ACTUAL_MANA_COLOR_FIELD + color
    if _split_actual_ability_color(enriched) != (context, color):
        raise ValueError("Actual color payment requires its exact ability context")
    return enriched


def actual_ability_payment_color(context):
    return _split_actual_ability_color(context)[1]


def _context_facts(
    spend_context: str | None,
) -> tuple[str | None, frozenset[str], frozenset[str], frozenset[str], bool, bool]:
    if spend_context and spend_context.startswith(_EXILED_SPELL_ANY_TYPE_PREFIX):
        parts = _read_exiled_spell_spending(spend_context)
        spend_context = parts[1] if parts else None
    if not spend_context:
        return None, frozenset(), frozenset(), frozenset(), False, False
    spend_context, _caster, _mode = _split_exile_play_caster(spend_context)
    spend_context, _colors = split_spell_colors(spend_context)
    spend_context, _actual = _split_actual_ability_color(spend_context)
    if spend_context == ROOM_UNLOCK_MANA_SPEND_CONTEXT:
        return ROOM_UNLOCK_SPEND_KIND, frozenset(), frozenset(), frozenset(), False, False
    if spend_context.startswith((_CONTEXT_PREFIX + ";", _BATTLEFIELD_CREATURE_ABILITY_CONTEXT_PREFIX + ";")):
        rows = spend_context.split(";")
        battlefield_creature_ability = rows[0] == _BATTLEFIELD_CREATURE_ABILITY_CONTEXT_PREFIX
        pairs = tuple(row.split("=", 1) for row in rows[1:] if "=" in row)
        fields = dict(pairs)
        cast_origin = fields.pop(_SPELL_ORIGIN_FIELD, None)
        if (
            len(pairs) != 4 + int(cast_origin is not None)
            or len(rows) != len(pairs) + 1
            or (cast_origin is not None and (cast_origin != "exile" or fields.get("kind") != "spell"))
            or set(fields) != {"kind", "types", "subtypes", "supertypes"}
            or fields["kind"] not in {"spell", "ability"}
            or (battlefield_creature_ability and fields["kind"] != "ability")
        ):
            return None, frozenset(), frozenset(), frozenset(), False, False
        values = tuple(
            frozenset(value for value in fields[field].split(",") if value)
            for field in ("types", "subtypes", "supertypes")
        )
        return fields["kind"], *values, battlefield_creature_ability, cast_origin == "exile"
    if spend_context in {"ability", "artifact_ability"}:
        return (
            "ability",
            frozenset({"artifact"} if spend_context == "artifact_ability" else ()),
            frozenset(),
            frozenset(),
            False,
            False,
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
            False,
            False,
        )
    return None, frozenset(), frozenset(), frozenset(), False, False


def mana_restriction_allows(
    restriction: str,
    spend_context: str | None,
) -> bool:
    """Return whether one typed restricted unit may pay in this context."""
    from .mana_spend_rules import decode_source_bound_cast_mana

    if decode_source_bound_cast_mana(restriction) is not None:
        return True

    kind, types, subtypes, supertypes, battlefield_creature_ability, spell_from_exile = _context_facts(spend_context)
    rule = decode_mana_spend_rule(restriction)
    if rule is not None:
        from .mana_spend_rules import ManaSpendRuleKind

        colors = _spell_context_colors(spend_context) if rule.kind is ManaSpendRuleKind.CHOSEN_MONOCOLORED_SPELL else ()
        return rule.allows(kind, types, subtypes, supertypes,
                           battlefield_creature_ability=battlefield_creature_ability, colors=colors,
                           spell_from_exile=spell_from_exile)
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


def mana_spend_protects_spell(restriction: str, spend_context: str | None) -> bool:
    rule = decode_mana_spend_rule(restriction)
    kind, types, subtypes, supertypes, _battlefield_creature_ability, _spell_from_exile = _context_facts(spend_context)
    return rule is not None and rule.protects(kind, types, subtypes, supertypes)


def mana_spend_haste_lifetime(restriction: str, spend_context: str | None):
    rule = decode_mana_spend_rule(restriction)
    if rule is None:
        return None
    kind, types, subtypes, _supertypes, _battlefield_creature_ability, _spell_from_exile = _context_facts(spend_context)
    return rule.haste_lifetime(kind, types, subtypes)


__all__ = [
    "ability_mana_spend_context",
    "bind_exiled_spell_mana_spending",
    "canonical_mana_spend_restriction",
    "exiled_spell_any_type_payer",
    "legacy_mana_spend_restriction_for_tail",
    "mana_restriction_allows",
    "spell_mana_spend_context",
    "valid_mana_spend_restriction",
]
