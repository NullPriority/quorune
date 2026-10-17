from __future__ import annotations

"""Closed Oracle surfaces for existing immutable public condition values."""

import re
from typing import Any, Collection, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..basic_land_types import LAND_SUBTYPES
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import source_self_permanent_type


def normalize_public_condition_surface(text: str) -> str:
    text = re.sub(r"\bthere['’]s(?=\s)", 'there is', text, flags=re.I)
    if text.casefold() == 'you had another creature enter the battlefield under your control this turn':
        return 'another creature entered the battlefield under your control this turn'
    source = re.fullmatch(r'(?P<subject>this [A-Za-z]+) entered this turn', text, re.I)
    if source is not None:
        kind = source_self_permanent_type(source['subject'])
        if kind is not None:
            return f'This {kind} entered this turn'
    return text


def fixed_condition_subject_extension(text: str) -> tuple[ObjectQuerySpec, bool] | None:
    land = re.fullmatch(r'(?P<other>other )?(?P<subtype>.+)', text, re.I)
    if land is not None:
        subtype = land['subtype'].casefold()
        if subtype not in LAND_SUBTYPES and subtype.endswith('s'):
            subtype = subtype[:-1]
        if subtype in LAND_SUBTYPES:
            return ObjectQuerySpec(zones=('battlefield',),types_all=('land',),
                subtypes_all=(subtype,)),bool(land['other'])
    match = re.fullmatch(r'(?P<other>other )?(?P<quality>colorless|nonbasic|snow|nontoken '
        r'(?:white|blue|black|red|green)) (?P<kind>artifact|battle|creature|enchantment|land|permanent|planeswalker)s?',
        text, re.I)
    if match is None:
        return None
    kind, quality = match['kind'].casefold(), match['quality'].casefold()
    fields: dict[str, Any] = {'zones': ('battlefield',), 'types_all': () if kind == 'permanent' else (kind,)}
    if quality == 'colorless':
        fields['colorless'] = True
    elif quality in {'nonbasic','snow'}:
        if kind != 'land':
            return None
        fields['excluded_supertypes' if quality=='nonbasic' else 'supertypes_all'] = (
            'basic' if quality=='nonbasic' else 'snow',)
    else:
        fields.update(token=False, colors_all=({'white':'W','blue':'U','black':'B','red':'R','green':'G'}[
            quality.removeprefix('nontoken ')],))
    return ObjectQuerySpec(**fields), bool(match['other'])


def fixed_condition_object_query(
    subject: str,
    quality: str,
    *,
    card_type_words: Collection[str],
    color_symbols: Mapping[str, str],
    noncreature_subtypes: Collection[str],
) -> ObjectQuerySpec | None:
    """Compile one positive public characteristic phrase through layer 5."""

    fields: dict[str, Any] = {"zones": ("battlefield",)}
    normalized_subject = subject.strip().casefold()
    normalized_quality = quality.strip().casefold()
    semantic_quality = re.sub(r"^(?:a|an)\s+", "", normalized_quality)
    if normalized_subject not in {
        "artifact",
        "creature",
        "enchantment",
        "land",
        "permanent",
        "planeswalker",
    }:
        return None
    if normalized_subject != "permanent":
        fields["types_all"] = (normalized_subject,)
    if normalized_quality in {"", "a", "an"}:
        return ObjectQuerySpec(**fields)
    if semantic_quality in card_type_words:
        fields["types_all"] = tuple(
            sorted({*fields.get("types_all", ()), semantic_quality})
        )
        return ObjectQuerySpec(**fields)
    if semantic_quality == "equipment":
        fields.update(types_all=("artifact",), subtypes_all=("equipment",))
        return ObjectQuerySpec(**fields)
    if semantic_quality in noncreature_subtypes:
        fields["subtypes_all"] = (semantic_quality,)
        return ObjectQuerySpec(**fields)
    if semantic_quality in color_symbols:
        fields["colors_all"] = (color_symbols[semantic_quality],)
        return ObjectQuerySpec(**fields)
    if semantic_quality == "legendary":
        fields["supertypes_all"] = ("legendary",)
        return ObjectQuerySpec(**fields)
    if semantic_quality.startswith("basic "):
        subtype = semantic_quality.removeprefix("basic ")
        if subtype not in {"plains", "island", "swamp", "mountain", "forest"}:
            return None
        fields.update(
            types_all=("land",),
            supertypes_all=("basic",),
            subtypes_all=(subtype,),
        )
        return ObjectQuerySpec(**fields)
    subtype_terms = re.fullmatch(
        r"(?:a|an) (?P<first>[A-Za-z][A-Za-z'-]*)"
        r"(?: or (?:a|an) (?P<second>[A-Za-z][A-Za-z'-]*))?",
        quality.strip(),
        re.IGNORECASE,
    )
    if subtype_terms is None:
        return None
    raw_subtypes = tuple(filter(None, subtype_terms.groups()))
    subtypes = tuple(
        subtype
        for raw in raw_subtypes
        if (
            subtype := (
                canonical_creature_subtype(raw)
                or (
                    raw.casefold()
                    if raw.casefold()
                    in noncreature_subtypes
                    else None
                )
            )
        ) is not None
    )
    if not subtypes or len(subtypes) != len(raw_subtypes):
        return None
    fields["subtypes_any"] = subtypes
    return ObjectQuerySpec(**fields)
